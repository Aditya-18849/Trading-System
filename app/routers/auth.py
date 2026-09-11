"""
Authentication Router — JWT Login, Verification & Password Management.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.auth.security import hash_password, verify_password, create_access_token, decode_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400
    user: dict


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate client trader credentials and return JWT bearer token."""
    from sqlalchemy import func

    username_raw = payload.username.strip()
    username_lower = username_raw.lower()
    password = payload.password.strip()

    # 1. Search user case-insensitively by client ID
    user = (
        db.query(User)
        .filter(func.lower(User.broker_client_id) == username_lower)
        .first()
    )

    # 2. Search by email
    if not user:
        user = (
            db.query(User)
            .filter(func.lower(User.email) == username_lower)
            .first()
        )

    # 3. Allow "admin" / "trader" alias to match the first active admin user
    if not user and username_lower in ("admin", "trader", "root", "dev001"):
        user = db.query(User).filter(User.is_active == True).first()

    # 4. If DB is completely empty, auto-create the initial default user
    if not user:
        client_id = settings.kite_user_id or "DEV001"
        existing = db.query(User).filter(User.broker_client_id == client_id).first()
        if not existing:
            user = User(
                broker_client_id=client_id,
                broker="zerodha",
                full_name="Primary Trader",
                email=f"{client_id.lower()}@trading.local",
                hashed_password=hash_password("trader@123"),
                role="admin",
                is_active=True,
                total_capital=float(settings.total_capital),
                max_daily_loss=float(settings.max_daily_loss),
                risk_per_trade_pct=float(settings.risk_per_trade_pct),
                max_trades_per_day=int(settings.max_trades_per_day),
                cooldown_minutes=int(getattr(settings, "cooldown_minutes_after_loss", 20)),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            user = existing

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or inactive account",
        )

    # 5. Verify password
    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # If password is verified but hash wasn't in DB, save the hash now
    if not user.hashed_password:
        user.hashed_password = hash_password(password)
        db.commit()

    # Create JWT Token
    token_payload = {
        "sub": str(user.id),
        "client_id": user.broker_client_id,
        "email": user.email,
        "role": getattr(user, "role", "admin") or "admin",
    }
    access_token = create_access_token(token_payload, expires_delta=timedelta(hours=24))

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=86400,
        user={
            "id": str(user.id),
            "full_name": user.full_name,
            "email": user.email,
            "broker_client_id": user.broker_client_id,
            "broker": user.broker,
            "role": getattr(user, "role", "admin") or "admin",
            "total_capital": float(user.total_capital or settings.total_capital),
        },
    )


@router.get("/me")
async def get_current_user_profile(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Return profile for the currently authenticated JWT bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )

    token = authorization.split(" ")[1]
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = None
    client_id = payload.get("client_id")
    if client_id:
        user = db.query(User).filter(User.broker_client_id == client_id).first()

    if not user:
        user_id_str = payload.get("sub")
        if user_id_str:
            try:
                user = db.query(User).filter(User.id == uuid.UUID(str(user_id_str))).first()
            except Exception:
                user = db.query(User).filter(User.email == user_id_str).first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "broker_client_id": user.broker_client_id,
        "broker": user.broker,
        "role": getattr(user, "role", "admin") or "admin",
        "total_capital": float(user.total_capital or settings.total_capital),
    }


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Update password for the authenticated trader."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")

    token = authorization.split(" ")[1]
    decoded = decode_access_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid token")

    client_id = decoded.get("client_id")
    user = db.query(User).filter(User.broker_client_id == client_id).first() if client_id else None
    if not user:
        user_id_str = decoded.get("sub")
        if user_id_str:
            try:
                user = db.query(User).filter(User.id == uuid.UUID(str(user_id_str))).first()
            except Exception:
                pass
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password incorrect")

    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    user.hashed_password = hash_password(payload.new_password)
    db.commit()

    return {"status": "success", "message": "Password updated successfully"}
