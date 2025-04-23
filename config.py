import os
import sys
from pydantic.dataclasses import dataclass
from dotenv import load_dotenv

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

@dataclass
class Settings:
    # Notion API 设置
    NOTION_API_KEY: str = notion_api_key or ""

    # API 设置
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # URL 设置
    PRESIGNED_URL_EXPIRATION: int = 3600  # 1 小时（秒）

    # 缓存设置
    CACHE_EXPIRATION: int = 300  # 5 分钟（秒）

settings = Settings()
