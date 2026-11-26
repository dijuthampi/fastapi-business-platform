"""API rate limiting configuration using slowapi."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Singleton rate limiter instance keyed by client IP address
limiter: Limiter = Limiter(key_func=get_remote_address)
