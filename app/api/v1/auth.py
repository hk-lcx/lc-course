from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import store
from app.auth.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models.schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut

router = APIRouter()

USERNAME_MIN, USERNAME_MAX = 3, 32
PASSWORD_MIN, PASSWORD_MAX = 6, 128


def _normalize_username(raw: str) -> str:
    username = raw.strip()
    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"账号长度需为 {USERNAME_MIN}-{USERNAME_MAX} 个字符",
        )
    if any(ch.isspace() for ch in username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="账号不能包含空白字符",
        )
    return username


def _validate_password(password: str) -> None:
    if not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"密码长度需为 {PASSWORD_MIN}-{PASSWORD_MAX} 个字符",
        )


def _auth_response(user_id: int, username: str, token_version: int = 0) -> AuthResponse:
    token, expires_in = create_access_token(user_id, username, token_version)
    return AuthResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserOut(id=user_id, username=username),
    )


@router.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest):
    """注册新账号，成功后直接返回登录凭证。"""
    username = _normalize_username(payload.username)
    _validate_password(payload.password)

    user_id = store.create_user(username, hash_password(payload.password))
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该账号已被注册",
        )

    return _auth_response(user_id, username)


@router.post("/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest):
    """账号密码登录。"""
    username = payload.username.strip()
    user = store.get_user_by_username(username)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _auth_response(user["id"], user["username"], user["token_version"])


@router.get("/auth/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    """返回当前登录用户。"""
    return UserOut(**current_user)


@router.post("/auth/logout")
def logout(current_user: dict = Depends(get_current_user)):
    """退出登录：作废该账号已签发的所有 token（含其它设备）。"""
    store.bump_token_version(current_user["id"])
    return {"success": True}
