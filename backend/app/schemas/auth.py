from enum import StrEnum

from pydantic import BaseModel, Field


class AuthRole(StrEnum):
    ADMIN = "admin"
    VIEWER = "viewer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: AuthRole
    user_id: int


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=120)
    password: str = Field(..., min_length=6)


class BootstrapRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=120)
    password: str = Field(..., min_length=8)


class AuthStatusResponse(BaseModel):
    needs_bootstrap: bool


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=120)
    password: str = Field(..., min_length=8)
    role: AuthRole = AuthRole.VIEWER


class UserRead(BaseModel):
    id: int
    username: str
    role: AuthRole

    class Config:
        from_attributes = True
