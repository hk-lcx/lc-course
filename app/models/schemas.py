from typing import Optional, List

from pydantic import BaseModel

# --- 2. 数据模型 ---
class ChatRequest(BaseModel):
    message: str
    image_url: Optional[str] = None
    thread_id: str
    # 八大菜系之一；为空时按原逻辑自由推荐
    cuisine: Optional[str] = None


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut