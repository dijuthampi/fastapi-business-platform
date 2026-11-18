from functools import lru_cache
from typing import List, Union

from pydantic import field_validator

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover
    import os
    from pydantic import BaseModel

    class BaseSettings(BaseModel):  # type: ignore[assignment, no-redef]
        """Fallback BaseSettings when pydantic-settings is not yet installed."""

        def __init__(self, **values):
            env_values = {}
            for field_name in type(self).model_fields.keys():
                if field_name in os.environ:
                    env_values[field_name] = os.environ[field_name]
            env_values.update(values)
            super().__init__(**env_values)

    class SettingsConfigDict(dict):  # type: ignore[no-redef]
        pass


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables."""

    # Project Information
    PROJECT_NAME: str = "FastAPI Business Platform"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "Production-grade FastAPI Business Platform"
    API_V1_STR: str = "/api/v1"

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # CORS Configuration
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:8000",
    ]

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v  # type: ignore[return-value]
        raise ValueError(v)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Return a cached singleton instance of Settings."""
    return Settings()


settings: Settings = get_settings()
