import os
from pydantic import Field
from pydantic.dataclasses import dataclass

# We'll read environment variables directly without dotenv

@dataclass
class Settings:
    # Notion API settings
    NOTION_API_KEY: str = os.getenv("NOTION_API_KEY", "")

    # S3 settings
    S3_BUCKET_NAME: str = "notion-s3-api"
    S3_REGION: str = "us-east-1"
    S3_ENDPOINT: str = "http://localhost:9000"  # Default for local testing

    # API settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # File settings
    TEMP_DIR: str = "temp"

    # URL settings
    PRESIGNED_URL_EXPIRATION: int = 3600  # 1 hour in seconds

    # Cache settings
    CACHE_EXPIRATION: int = 300  # 5 minutes in seconds

settings = Settings()
