import re
import urllib.parse
import uuid
from datetime import datetime, timezone
import hashlib
from typing import Dict, Any, Optional, Tuple

from models import NotionIdType


def decode_url_encoding(text: str) -> str:
    """
    解码 URL 编码的文本，特别是处理中文字符
    """
    try:
        # 处理 %XX 编码
        return urllib.parse.unquote(text)
    except Exception:
        return text


def generate_etag(content: str) -> str:
    """
    为内容生成 ETag
    """
    return hashlib.md5(content.encode()).hexdigest()


def format_datetime_for_browser(dt: datetime) -> str:
    """
    格式化日期时间以便在浏览器中以本地时区显示
    """
    # 转换为带时区信息的 ISO 格式
    return dt.astimezone(timezone.utc).isoformat()


def detect_notion_id_type(notion_id: str) -> Tuple[NotionIdType, str]:
    """
    检测 Notion ID 的类型（页面、块、数据库）
    并规范化 ID 格式
    """
    # 处理可能的 URL
    if notion_id.startswith("http"):
        # 从 URL 中提取 ID
        parts = notion_id.split("/")
        for part in parts:
            if len(part.replace("-", "")) >= 32:
                notion_id = part
                break
        print(f"从 URL 提取的 ID: {notion_id}")

    # 移除 ID 中的任何破折号
    normalized_id = notion_id.replace("-", "")

    # 如果 ID 太短，则无效
    if len(normalized_id) < 32:
        print(f"警告: ID 太短 ({len(normalized_id)} < 32)")
        return NotionIdType.UNKNOWN, notion_id

    # 如果 ID 太长，截取前 32 个字符
    if len(normalized_id) > 32:
        print(f"警告: ID 太长 ({len(normalized_id)} > 32)，截取前 32 个字符")
        normalized_id = normalized_id[:32]

    # 确保 ID 以正确的格式用于 Notion API（带破折号）
    formatted_id = f"{normalized_id[:8]}-{normalized_id[8:12]}-{normalized_id[12:16]}-{normalized_id[16:20]}-{normalized_id[20:]}"
    print(f"格式化后的 ID: {formatted_id}")

    # 我们需要进行 API 调用来确定确切类型
    # 现在，返回 UNKNOWN 并让调用者确定类型
    return NotionIdType.UNKNOWN, formatted_id


def generate_s3_key(notion_object_id: str, parent_path: str = "", name: str = "") -> str:
    """
    为 Notion 对象生成 S3 键
    """
    if parent_path:
        if not parent_path.endswith("/"):
            parent_path += "/"

    if name:
        return f"{parent_path}{name}"

    return f"{parent_path}{notion_object_id}"


def parse_s3_key(key: str) -> Dict[str, str]:
    """
    解析 S3 键以提取路径组件
    """
    parts = key.split("/")
    result = {
        "full_path": key,
        "name": parts[-1] if parts else "",
        "parent_path": "/".join(parts[:-1]) if len(parts) > 1 else "",
    }
    return result


def is_file_block(block: Dict[str, Any]) -> bool:
    """
    检查块是否表示文件
    """
    # 扩展支持的文件类型
    file_block_types = [
        # 基本文件类型
        "file", "pdf",
        # 图片类型
        "image",
        # 多媒体类型
        "video", "audio",
        # 其他媒体类型
        "media", "file_attachment", "document", "spreadsheet", "presentation"

        # 以下类型已注释，不再支持提取
        # # 嵌入类型
        # "embed", "bookmark", "link_preview", "link_to_page",
        # # 代码和数据类型
        # "code", "equation",
        # # 其他可能包含文件的块
        # "callout", "synced_block", "template", "column_list", "column",
        # # 数据库相关
        # "child_database", "child_page", "table", "table_row",
        # # 外部服务集成
        # "external", "drive", "figma", "framer", "gist", "maps", "miro", "typeform", "codepen",
        # # 其他可能的块类型
        # "divider", "table_of_contents", "breadcrumb", "bulleted_list_item", "numbered_list_item"
    ]

    block_type = block.get("type")

    # 检查基本类型
    if block_type in file_block_types:
        return True

    # 检查块是否有文件 URL
    if block_type and block_type in block:
        block_content = block[block_type]

        # 检查是否有 URL 属性
        if isinstance(block_content, dict) and "url" in block_content:
            return True

        # 检查是否有文件属性
        if isinstance(block_content, dict) and "file" in block_content:
            return True

        # 检查是否有外部文件属性
        if isinstance(block_content, dict) and "external" in block_content:
            return True

    return False


def get_browser_timezone() -> str:
    """
    获取浏览器的时区（如果不可用，默认为 UTC）
    """
    # 在实际实现中，这将从请求中确定
    return "UTC"


def convert_to_browser_timezone(dt: datetime, timezone_str: str = "UTC") -> datetime:
    """
    将日期时间转换为浏览器的时区
    """
    # 在实际实现中，这将转换为指定的时区
    return dt.astimezone(timezone.utc)
