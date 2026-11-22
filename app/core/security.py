"""Security utilities: password hashing, verification, and JWT access token handling."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException, status

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password Hashing and Verification
# ---------------------------------------------------------------------------
# Priority 1: pwdlib with Argon2 (Modern recommended standard in FastAPI)
# Priority 2: passlib with bcrypt (Classic fallback)
# Priority 3: hashlib PBKDF2 (Zero-dependency fallback if running before pip install)
try:
    from pwdlib import PasswordHash
    from pwdlib.hashers.argon2 import Argon2Hasher

    _password_hash = PasswordHash((Argon2Hasher(),))

    def hash_password(password: str) -> str:
        """Hash a plain text password using Argon2."""
        return _password_hash.hash(password)

    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a plain password against its Argon2 hash."""
        return _password_hash.verify(plain_password, hashed_password)

except ImportError:  # pragma: no cover
    try:
        from passlib.context import CryptContext

        _pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

        def hash_password(password: str) -> str:
            """Hash a plain text password using Bcrypt."""
            return _pwd_context.hash(password)

        def verify_password(plain_password: str, hashed_password: str) -> bool:
            """Verify a plain password against its Bcrypt hash."""
            return _pwd_context.verify(plain_password, hashed_password)

    except ImportError:  # pragma: no cover
        import hashlib
        import hmac

        def hash_password(password: str) -> str:
            """Fallback password hashing using PBKDF2-HMAC-SHA256."""
            salt = "fastapi_salt_"
            derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
            return f"pbkdf2_sha256${derived.hex()}"

        def verify_password(plain_password: str, hashed_password: str) -> bool:
            """Fallback password verification using constant-time comparison."""
            salt = "fastapi_salt_"
            derived = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt.encode(), 100000)
            expected = f"pbkdf2_sha256${derived.hex()}"
            return hmac.compare_digest(expected, hashed_password)


# ---------------------------------------------------------------------------
# JWT Access Token Management
# ---------------------------------------------------------------------------
try:
    import jwt
    from jwt.exceptions import ExpiredSignatureError, InvalidTokenError, PyJWTError
except ImportError:  # pragma: no cover
    import base64
    import hashlib
    import hmac
    import json

    class PyJWTError(Exception):
        pass

    class InvalidTokenError(PyJWTError):
        pass

    class ExpiredSignatureError(PyJWTError):
        pass

    class _JWTFallback:
        @staticmethod
        def encode(payload: dict, key: str, algorithm: str = "HS256") -> str:
            header = {"typ": "JWT", "alg": algorithm}
            b64_header = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip("=")
            # Convert datetime objects to timestamps
            serializable_payload = {}
            for k, v in payload.items():
                if isinstance(v, datetime):
                    serializable_payload[k] = int(v.timestamp())
                else:
                    serializable_payload[k] = v
            b64_payload = base64.urlsafe_b64encode(json.dumps(serializable_payload).encode()).decode().rstrip("=")
            signing_input = f"{b64_header}.{b64_payload}".encode()
            sig = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
            b64_sig = base64.urlsafe_b64encode(sig).decode().rstrip("=")
            return f"{b64_header}.{b64_payload}.{b64_sig}"

        @staticmethod
        def decode(token: str, key: str, algorithms: list[str]) -> dict:
            parts = token.split(".")
            if len(parts) != 3:
                raise InvalidTokenError("Invalid token format")
            b64_header, b64_payload, b64_sig = parts
            signing_input = f"{b64_header}.{b64_payload}".encode()
            expected_sig = hmac.new(key.encode(), signing_input, hashlib.sha256).digest()
            # Add padding
            rem = len(b64_sig) % 4
            sig_to_check = b64_sig + ("=" * (4 - rem) if rem else "")
            try:
                decoded_sig = base64.urlsafe_b64decode(sig_to_check)
            except Exception:
                raise InvalidTokenError("Invalid signature encoding")
            if not hmac.compare_digest(expected_sig, decoded_sig):
                raise InvalidTokenError("Signature mismatch")
            payload_rem = len(b64_payload) % 4
            payload_b64 = b64_payload + ("=" * (4 - payload_rem) if payload_rem else "")
            payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
            if "exp" in payload:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                if now_ts >= payload["exp"]:
                    raise ExpiredSignatureError("Token has expired")
            return payload

    jwt = _JWTFallback()  # type: ignore[assignment]


def create_access_token(
    subject: str | UUID,
    organization_id: str | UUID,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Generate an encoded JWT access token with tenant and role claims."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode: Dict[str, Any] = {
        "sub": str(subject),
        "org_id": str(organization_id),
        "role": str(role),
        "iat": now,
        "exp": expire,
    }

    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return str(encoded_jwt)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate a JWT access token.

    Raises:
        HTTPException: 401 Unauthorized if token is expired or malformed.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    expired_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token has expired",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except ExpiredSignatureError:
        raise expired_exception
    except (InvalidTokenError, PyJWTError, Exception):
        raise credentials_exception
