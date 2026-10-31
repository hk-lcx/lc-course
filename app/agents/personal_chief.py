import asyncio

from langchain import messages
from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch
from langchain.agents import create_agent
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
from app.common.logger import logger
from langchain.messages import HumanMessage,AIMessage,AIMessageChunk
import os

#1.加载环境变量
from dotenv import load_dotenv
load_dotenv()

#2.web搜索工具，使用tavily作为web搜索工具
web_search=TavilySearch(
    max_results=5,
    topic="general"
)

#3.多模态模型（DashScope OpenAI 兼容接口）
model=init_chat_model(
    model="qwen3.8-omni-flash",
    model_provider="openai",
    base_url=os.getenv("DASHSCOPE_BASE_URL"),
    api_key=os.getenv("DASHSCOPE_API_KEY")
)

#4.初始化checkpointer
#连接sqlite
connection=sqlite3.connect("../db/personal_chief.db",check_same_thread=False)
#初始化checkpointer
checkpointer=SqliteSaver(connection)
#自动建表
checkpointer.setup()

#4.Agent系统提示词
system_prompt="""
你是一名私人厨师。用户会指定八大菜系（川菜、鲁菜、粤菜、苏菜、浙菜、闽菜、湘菜、徽菜）中的一个，用户消息中会以【本次菜系】标注。你必须只检索和推荐该菜系下的菜谱，不要推荐其他菜系的菜；如果用户没有指定菜系，再按食材自由推荐。

收到用户提供的食材照片或清单后，请按以下流程操作:
1.识别和评估食材:若用户提供照片，首先辨识所有可见食材。基于食材的外观状态，评估其新鲜度与可用量，整理出一份“当前可用食材清单”。
2.智能食谱检索:优先调用web_search 工具，以“可用食材清单”为核心关键词，查找可行菜谱。
3.多维度评估与排序:从营养价值和制作难度两个维度对检索到的候选食谱进行量化打分，并根据得分排序，制作简单且营养丰富的排名靠前。
4，结构化方案输出:把排序后的食谱整理为一份结构清晰的建议报告，要包含食谱信息、得分、推荐理由、食谱的参考图片，帮助用户快速做出决策。

请严格按照流程，优先调web_search 工具搜索食谱，搜索不到的情况下才能自己发挥。
"""

#创建Agent
agent=create_agent(
    model=model,#模型
    tools=[web_search],#工具
    checkpointer=checkpointer,#记忆
    system_prompt=system_prompt#系统提示词
)

#流式对话
async def search_recipes(prompt:str,image:str,thread_id:str,cuisine:str=""):
    """调用agent搜索食谱"""
    logger.info(f"[用户]:{prompt}, cuisine:{cuisine}, image:{image},thread_id:{thread_id}")
    try:
        # 把菜系要求显式写进用户消息，约束检索和推荐范围
        text = prompt
        if cuisine:
            text = f"【本次菜系】{cuisine}\n请只推荐{cuisine}的菜谱。\n用户提供的食材/需求：{prompt}"

        #判断是否有图片，封装不同格式的消息
        if not image or image.strip()=="":
            message=HumanMessage(content=text)
        else:
            message=HumanMessage(content=[
                {"type": "image","url":image},
                {"type": "text","text":text}
            ])

        #流式调用Agent
        for chunk, metadata in agent.stream(
            {"messages":[message]},
            {"configurable":{"thread_id":thread_id}},
            stream_mode="messages"
        ):
            if isinstance(chunk,AIMessageChunk) and chunk.content:
                yield chunk.content
                # 同步迭代器会一直占住事件循环；每片之后让出一次，
                # 浏览器才能真正收到分片（否则要等全部生成完才一次性发出）。
                await asyncio.sleep(0)

    except Exception as e:
        logger.error(f"\n[错误]:{str(e)}")
        yield "信息检索失败，试试看手动输入食物列表？"

#清空会话
def clear_message(thread_id:str):
    """清空会话"""
    logger.info(f"清空历史消息，thread_id:{thread_id}")
    checkpointer.delete_thread(thread_id)

#查询会话历史
def get_message(thread_id:str) -> list[dict[str, str]]:
    """获取会话历史"""
    logger.info(f"thread_id:{thread_id}")
    #根据thread_id查询checkpoint
    checkpoint=checkpointer.get({"configurable":{"thread_id":thread_id}})
    #如果不存在，返回空列表
    if not checkpoint:
        return []
    #安全获取messages
    channel_values=checkpoint.get("channel_values")
    if not channel_values:
        return []

    messages=channel_values.get("messages",[])
    if not messages:
        return []
    #转换消息格式
    result=[]
    for msg in messages:
        if not msg.content:
            continue

        if isinstance(msg.content,HumanMessage):
            result.append({"type": "text","text":msg.content})
        elif isinstance(msg.content,AIMessage):
            result.append({"type": "text","text":msg.content})

    return result