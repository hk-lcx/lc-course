from fastapi import APIRouter, Depends, HTTPException, status
from app.models.schemas import ChatRequest
from app.agents.personal_chief import search_recipes,get_message,clear_message
from app.auth.security import get_current_user
from app.common.cuisines import CUISINES
from fastapi.responses import StreamingResponse

router = APIRouter()


def _user_thread_id(user_id: int, thread_id: str) -> str:
    """在 checkpointer 中按用户隔离会话，避免跨账号读到同一个 thread 的历史。"""
    return f"{user_id}:{thread_id}"


@router.post("/chat/stream")
async def chat_endpoint(request: ChatRequest, current_user: dict = Depends(get_current_user)):
    """流式对话"""
    cuisine = (request.cuisine or "").strip()
    if cuisine and cuisine not in CUISINES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的菜系，请从八大菜系中选择",
        )

    return StreamingResponse(
        search_recipes(
            request.message,
            request.image_url,
            _user_thread_id(current_user["id"], request.thread_id),
            cuisine=cuisine,
        ),
        media_type="text/event-stream",
        headers={
            # 防止浏览器 / Nginx / 其它反代把 token 攒起来一次性发出
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/messages")
async def get_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """获取历史消息"""
    message = get_message(_user_thread_id(current_user["id"], thread_id))
    return {"messages":message}

@router.delete("/chat/messages")
async def clear_chat_messages(thread_id: str, current_user: dict = Depends(get_current_user)):
    """清空历史消息"""
    clear_message(_user_thread_id(current_user["id"], thread_id))
    return {"success":True}
