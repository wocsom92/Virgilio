from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import get_session
from backend.app.models.users import User, UserRole


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
# Use pbkdf2_sha256 to avoid bcrypt native backend issues and 72-byte limits.
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Validate a password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plaintext password for storage."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.auth_access_token_exp_minutes)
    )
    to_encode.update({"exp": expire})
    secret = settings.auth_secret_key or settings.admin_api_token
    return jwt.encode(to_encode, secret, algorithm=settings.auth_algorithm)


async def _get_current_user(token: str, session: AsyncSession) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.auth_secret_key or settings.admin_api_token, algorithms=[settings.auth_algorithm])
        username: str | None = payload.get("sub")
        role: str | None = payload.get("role")
        if username is None or role is None:
            raise credentials_error
    except JWTError as exc:
        raise credentials_error from exc

    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_error
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Return the authenticated user or raise 401."""
    return await _get_current_user(token, session)


async def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Ensure the authenticated user has admin privileges."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
