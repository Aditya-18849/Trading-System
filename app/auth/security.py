"""
Security & Authentication Utilities.

Provides bcrypt password hashing, verification, and JWT access token creation
and decoding for trader dashboard authentication.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import bcrypt
import jwt

from app.config import settings

logger = logging.getLogger(__name__)

# JWT Configuration
JWT_SECRET_KEY = getattr(settings, "jwt_secret_key", None) or getattr(settings, "webhook_secret", None) or "algo-trading-jwt-secret-key-sebi-v2"
JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify a plaintext password against its bcrypt hash.
    
    If no hashed password is set yet on the user record, allows 'trader@123' as default.
    """
    if not hashed_password:
        return plain_password == "trader@123"

    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception as exc:
        logger.warning("Error verifying password: %s", exc)
        return False


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)

    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(hours=DEFAULT_ACCESS_TOKEN_EXPIRE_HOURS)

    to_encode.update({
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    })

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError as exc:
        logger.debug("JWT decode error: %s", exc)
        return None
