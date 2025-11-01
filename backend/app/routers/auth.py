from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    require_admin_user,
    verify_password,
)
from backend.app.db.session import get_session
from backend.app.models.users import User, UserRole
from backend.app.schemas.auth import (
    AuthRole,
    AuthStatusResponse,
    BootstrapRequest,
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserRead,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(session: AsyncSession = Depends(get_session)) -> AuthStatusResponse:
    first_user = await session.scalar(select(User.id).limit(1))
    return AuthStatusResponse(needs_bootstrap=first_user is None)


@router.post("/bootstrap", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def bootstrap_admin(
    payload: BootstrapRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    existing = await session.scalar(select(User.id).limit(1))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin already configured",
        )

    username = payload.username.strip()
    hashed = get_password_hash(payload.password)
    user = User(username=username, hashed_password=hashed, role=UserRole.ADMIN.value)
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token({"sub": user.username, "role": user.role}, expires_delta=timedelta(days=2))
    return TokenResponse(access_token=token, username=user.username, role=AuthRole.ADMIN, user_id=user.id)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    username = payload.username.strip()
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid username or password",
        )

    token = create_access_token({"sub": user.username, "role": user.role})
    return TokenResponse(access_token=token, username=user.username, role=AuthRole(user.role), user_id=user.id)


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.get(
    "/users",
    response_model=list[UserRead],
    dependencies=[Depends(require_admin_user)],
)
async def list_users(session: AsyncSession = Depends(get_session)) -> list[UserRead]:
    result = await session.execute(select(User))
    users = list(result.scalars())
    return [UserRead.model_validate(user) for user in users]


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_user)],
)
async def create_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")

    result = await session.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    hashed = get_password_hash(payload.password)
    user = User(username=username, hashed_password=hashed, role=payload.role.value)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserRead.model_validate(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_user)],
)
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")

    if user.role == UserRole.ADMIN.value:
        admin_count = await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMIN.value))
        if admin_count is not None and admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one admin account is required",
            )

    await session.delete(user)
    await session.commit()
