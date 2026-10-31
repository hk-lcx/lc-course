"""密码哈希（stdlib scrypt）与 JWT 签发/校验。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import store

load_dotenv()

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16

_JWT_ALGORITHM = "HS256"
_JWT_SECRET = os.getenv("AUTH_SECRET")
if not _JWT_SECRET:
    raise RuntimeError(
        "缺少 AUTH_SECRET 环境变量：请在 .env 中配置一个足够长的随机字符串（例如 `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`）"
    )

_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_HOURS", "168")) * 3600

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """返回 scrypt 哈希，格式：scrypt$n$r$p$salt_b64$hash_b64。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    user_id: int, username: str, token_version: int = 0
) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_TOKEN_TTL_SECONDS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "ver": int(token_version),
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
    return token, _TOKEN_TTL_SECONDS


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """FastAPI 依赖：校验 Bearer token 并返回当前用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("未登录，请先登录")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _unauthorized("登录已失效，请重新登录")

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise _unauthorized("登录凭证无效")

    user = store.get_user_by_id(user_id)
    if user is None:
        raise _unauthorized("用户不存在")

    # 退出登录会递增 token_version，旧 token 的 ver 对不上即失效
    if int(payload.get("ver", 0)) != int(user["token_version"]):
        raise _unauthorized("登录已失效，请重新登录")

    return {"id": user["id"], "username": user["username"]}
