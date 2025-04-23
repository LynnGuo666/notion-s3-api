import os
import sys
from pydantic.dataclasses import dataclass
from dotenv import load_dotenv

# 版本号
VERSION = "1.2.3"

# 加载 .env 文件
load_dotenv()

# 检查是否有 Notion API 密钥
notion_api_key = os.getenv("NOTION_API_KEY")
if not notion_api_key:
    print("错误: 没有找到 Notion API 密钥，请在 .env 文件中设置 NOTION_API_KEY")
    print("当前工作目录:", os.getcwd())
    print(".env 文件内容:")
    try:
        with open(".env", "r") as f:
            print(f.read())
    except Exception as e:
        print(f"无法读取 .env 文件: {e}")

# 获取 API 密钥
api_key = os.getenv("API_KEY", "")

# AWS S3 凭据
s3_access_key_id = os.getenv("S3_ACCESS_KEY_ID", "")
s3_secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY", "")

@dataclass
class Settings:
    # 版本号
    VERSION: str = VERSION

    # Notion API 设置
    NOTION_API_KEY: str = notion_api_key or ""

    # API 设置
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_KEY: str = api_key  # API 访问密钥

    # AWS S3 凭据
    S3_ACCESS_KEY_ID: str = s3_access_key_id  # AWS S3 访问密钥 ID
    S3_SECRET_ACCESS_KEY: str = s3_secret_access_key  # AWS S3 秘密访问密钥

    # URL 设置
    PRESIGNED_URL_EXPIRATION: int = 3600  # 1 小时（秒）

    # 缓存设置
    CACHE_EXPIRATION: int = 300  # 5 分钟（秒）

    # 性能设置
    MAX_CONCURRENT_REQUESTS: int = 20  # 并发请求数
    REQUEST_TIMEOUT: int = 60  # 请求超时时间（秒）
    LONG_POLLING_TIMEOUT: int = 300  # 长轮询超时时间（秒）

settings = Settings()
