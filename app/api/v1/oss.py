import alibabacloud_oss_v2 as oss
from fastapi import APIRouter, Depends
from datetime import timedelta
import os
from urllib.parse import quote
# 加载环境变量
from dotenv import load_dotenv

from app.auth.security import get_current_user

load_dotenv()
router = APIRouter()

# 从环境变量中加载凭证信息，用于身份验证
credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

# 加载SDK的默认配置，并设置凭证提供者
cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider

# 方式一：只填写Region（推荐）
# 必须指定Region ID，SDK会根据Region自动构造HTTPS访问域名
cfg.region = 'cn-beijing'

# 使用配置好的信息创建OSS客户端
client = oss.Client(cfg)

# OSS 域名配置
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")
OSS_BUCKET = os.getenv("OSS_BUCKET")


@router.get("/oss/presign")
def presign_endpoint(filename: str, current_user: dict = Depends(get_current_user)):
    # 根据文件扩展名判断 Content-Type
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    # 只取文件名，避免 `../` 之类的路径注入；空文件名给个兜底
    safe_name = os.path.basename(filename.replace("\\", "/")).strip() or "image.jpg"
    ext = safe_name.split(".")[-1].lower() if "." in safe_name else "jpg"
    content_type = content_type_map.get(ext, "application/octet-stream")

    # 每个用户的图片放在独立前缀下，避免互相覆盖
    key = f"u{current_user['id']}/{safe_name}"

    pre_result = client.presign(oss.PutObjectRequest(
        bucket=OSS_BUCKET,
        key=key,
        content_type=content_type,
    ), expires=timedelta(seconds=3600))

    # 返回上传 URL 和可访问的图片路径
    return {
        "uploadUrl": pre_result.url.strip('"'),
        "contentType": content_type,
        "accessUrl": f"https://{OSS_BUCKET}.{OSS_ENDPOINT}/{quote(key, safe='/')}"
    }
