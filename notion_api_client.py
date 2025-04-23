import os
import json
from typing import Dict, List, Optional, Any, Tuple, Set
import urllib.parse
from datetime import datetime, timedelta

from notion_client import Client
from notion_client.errors import APIResponseError

from config import settings
from models import NotionIdType, NotionObject, NotionFile, NotionFolder
from utils import detect_notion_id_type, decode_url_encoding, is_file_block


class NotionAPI:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or settings.NOTION_API_KEY

        if not self.api_key:
            raise ValueError("需要提供 Notion API 密钥，请在 .env 文件中设置 NOTION_API_KEY")

        print(f"使用 Notion API 密钥: {self.api_key[:5]}...{self.api_key[-5:]}")
        self.client = Client(auth=self.api_key)
        self.cache = {}
        self.cache_expiration = {}

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """Get data from cache if it exists and is not expired"""
        if key in self.cache and key in self.cache_expiration:
            if datetime.now() < self.cache_expiration[key]:
                return self.cache[key]
        return None

    def _add_to_cache(self, key: str, data: Any) -> None:
        """Add data to cache with expiration"""
        self.cache[key] = data
        self.cache_expiration[key] = datetime.now() + timedelta(seconds=settings.CACHE_EXPIRATION)

    async def identify_id_type(self, notion_id: str) -> Tuple[NotionIdType, Dict[str, Any]]:
        """
        Identify the type of a Notion ID (page, block, database)
        and return the object data
        """
        # Check cache first
        cache_key = f"id_type_{notion_id}"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data

        # Normalize the ID format
        id_type, formatted_id = detect_notion_id_type(notion_id)
        if id_type == NotionIdType.UNKNOWN:
            # Try to retrieve as different types
            try:
                # Try as page
                page_data = self.client.pages.retrieve(formatted_id)
                result = (NotionIdType.PAGE, page_data)
                self._add_to_cache(cache_key, result)
                return result
            except APIResponseError as e:
                print(f"Not a page: {e}")
                try:
                    # Try as database
                    db_data = self.client.databases.retrieve(formatted_id)
                    result = (NotionIdType.DATABASE, db_data)
                    self._add_to_cache(cache_key, result)
                    return result
                except APIResponseError as e:
                    print(f"Not a database: {e}")
                    try:
                        # Try as block
                        block_data = self.client.blocks.retrieve(formatted_id)
                        result = (NotionIdType.BLOCK, block_data)
                        self._add_to_cache(cache_key, result)
                        return result
                    except APIResponseError as e:
                        # Unknown or inaccessible
                        print(f"Not a block: {e}")
                        print(f"API Key: {self.api_key[:5]}...{self.api_key[-5:]}")
                        print(f"Formatted ID: {formatted_id}")
                        return NotionIdType.UNKNOWN, {}

        return id_type, {}

    async def get_page_title(self, page_id: str) -> str:
        """Get the title of a page"""
        try:
            page = self.client.pages.retrieve(page_id)
            # Extract title from properties
            title_prop = None
            for prop_name, prop_data in page.get("properties", {}).items():
                if prop_data.get("type") == "title":
                    title_prop = prop_data
                    break

            if title_prop and "title" in title_prop:
                title_parts = title_prop["title"]
                return "".join([part.get("plain_text", "") for part in title_parts])
            return f"Untitled Page ({page_id})"
        except Exception as e:
            return f"Untitled Page ({page_id})"

    async def get_database_title(self, database_id: str) -> str:
        """Get the title of a database"""
        try:
            db = self.client.databases.retrieve(database_id)
            title_parts = db.get("title", [])
            return "".join([part.get("plain_text", "") for part in title_parts])
        except Exception as e:
            return f"Untitled Database ({database_id})"

    async def get_block_title(self, block_id: str) -> str:
        """Get a representative title for a block"""
        try:
            block = self.client.blocks.retrieve(block_id)
            block_type = block.get("type", "")

            # Different block types have different title representations
            if block_type == "heading_1" or block_type == "heading_2" or block_type == "heading_3":
                text_parts = block.get(block_type, {}).get("rich_text", [])
                return "".join([part.get("plain_text", "") for part in text_parts])
            elif block_type == "paragraph":
                text_parts = block.get("paragraph", {}).get("rich_text", [])
                text = "".join([part.get("plain_text", "") for part in text_parts])
                # Truncate long paragraphs
                return text[:50] + "..." if len(text) > 50 else text
            elif is_file_block(block):
                return f"{block_type.capitalize()} Block"
            else:
                return f"{block_type.capitalize()} Block ({block_id})"
        except Exception as e:
            return f"Block ({block_id})"

    async def get_object_title(self, notion_id: str) -> str:
        """Get the title of any Notion object based on its ID"""
        id_type, _ = await self.identify_id_type(notion_id)

        if id_type == NotionIdType.PAGE:
            return await self.get_page_title(notion_id)
        elif id_type == NotionIdType.DATABASE:
            return await self.get_database_title(notion_id)
        elif id_type == NotionIdType.BLOCK:
            return await self.get_block_title(notion_id)
        else:
            return f"Unknown Object ({notion_id})"

    async def get_children(self, parent_id: str, id_type: NotionIdType) -> List[Dict[str, Any]]:
        """Get all children of a parent object"""
        cache_key = f"children_{parent_id}"
        cached_data = self._get_from_cache(cache_key)
        if cached_data:
            return cached_data

        children = []

        if id_type == NotionIdType.PAGE or id_type == NotionIdType.BLOCK:
            # Get block children
            has_more = True
            start_cursor = None

            while has_more:
                response = self.client.blocks.children.list(
                    block_id=parent_id,
                    start_cursor=start_cursor
                )

                children.extend(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")

        elif id_type == NotionIdType.DATABASE:
            # Query database
            has_more = True
            start_cursor = None

            while has_more:
                response = self.client.databases.query(
                    database_id=parent_id,
                    start_cursor=start_cursor
                )

                children.extend(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")

        self._add_to_cache(cache_key, children)
        return children

    async def get_all_subpages_recursive(self, parent_id: str, visited: Optional[Set[str]] = None) -> Dict[str, NotionObject]:
        """
        Recursively get all subpages, including subpages of subpages,
        database subpages, etc.
        """
        if visited is None:
            visited = set()

        if parent_id in visited:
            return {}

        visited.add(parent_id)
        result = {}

        # Identify the parent type
        id_type, obj_data = await self.identify_id_type(parent_id)

        if id_type == NotionIdType.UNKNOWN:
            return result

        # Get the parent title
        title = await self.get_object_title(parent_id)

        # Add the parent to the result
        parent_obj = NotionObject(
            id=parent_id,
            type=id_type,
            title=title,
            created_time=datetime.fromisoformat(obj_data.get("created_time", datetime.now().isoformat())),
            last_edited_time=datetime.fromisoformat(obj_data.get("last_edited_time", datetime.now().isoformat())),
            url=obj_data.get("url", "")
        )
        result[parent_id] = parent_obj

        # Get children
        children = await self.get_children(parent_id, id_type)

        for child in children:
            child_id = child.get("id")
            child_type = child.get("type")

            # If child is a page or database, recursively get its subpages
            if child_type == "child_page" or child_type == "child_database":
                child_pages = await self.get_all_subpages_recursive(child_id, visited)
                result.update(child_pages)
            elif id_type == NotionIdType.DATABASE:
                # Database query results are pages
                child_pages = await self.get_all_subpages_recursive(child_id, visited)
                result.update(child_pages)

        return result

    async def get_files_from_page(self, page_id: str) -> List[NotionFile]:
        """Get all files from a page"""
        files = []

        # Get all blocks in the page
        blocks = await self.get_children(page_id, NotionIdType.PAGE)

        for block in blocks:
            block_id = block.get("id")
            block_type = block.get("type")

            # Check if this is a file block
            if is_file_block(block):
                print(f"\n找到文件块: {block_id}, 类型: {block_type}")

                # 尝试不同的方式提取 URL
                url = ""
                filename = f"file_{block_id}.{block_type}"

                # 方式 1: 直接从块内容中提取 URL
                if block_type in block:
                    block_content = block[block_type]

                    # 检查是否有直接的 URL
                    if isinstance(block_content, dict) and "url" in block_content:
                        url = block_content["url"]
                        print(f"  从块内容直接提取 URL: {url}")

                    # 检查是否有类型字段
                    elif isinstance(block_content, dict) and "type" in block_content:
                        content_type = block_content["type"]
                        if content_type in block_content and "url" in block_content[content_type]:
                            url = block_content[content_type]["url"]
                            print(f"  从内容类型提取 URL: {url}")

                    # 检查是否有文件字段
                    elif isinstance(block_content, dict) and "file" in block_content:
                        if "url" in block_content["file"]:
                            url = block_content["file"]["url"]
                            print(f"  从文件字段提取 URL: {url}")

                    # 检查是否有外部字段
                    elif isinstance(block_content, dict) and "external" in block_content:
                        if "url" in block_content["external"]:
                            url = block_content["external"]["url"]
                            print(f"  从外部字段提取 URL: {url}")

                    # 检查是否有标题或名称
                    if isinstance(block_content, dict):
                        if "title" in block_content:
                            title_parts = block_content["title"]
                            if isinstance(title_parts, list):
                                title = "".join([part.get("plain_text", "") for part in title_parts])
                                if title:
                                    filename = title
                                    print(f"  从标题提取文件名: {filename}")
                        elif "caption" in block_content:
                            caption_parts = block_content["caption"]
                            if isinstance(caption_parts, list):
                                caption = "".join([part.get("plain_text", "") for part in caption_parts])
                                if caption:
                                    filename = caption
                                    print(f"  从标题提取文件名: {filename}")

                # 方式 2: 传统方式提取
                if not url:
                    file_data = block.get(block_type, {})
                    file_type = file_data.get("type", "external")
                    file_info = file_data.get(file_type, {})
                    url = file_info.get("url", "")
                    print(f"  传统方式提取 URL: {url}")

                if url:
                    # 从 URL 提取文件名
                    parsed_url = urllib.parse.urlparse(url)
                    path = parsed_url.path
                    url_filename = os.path.basename(path)

                    # 如果没有从块内容提取到文件名，则使用 URL 中的文件名
                    if filename.startswith("file_"):
                        filename = url_filename

                    # 解码 URL 编码字符
                    filename = decode_url_encoding(filename)
                    print(f"  最终文件名: {filename}")

                    # 创建 NotionFile 对象
                    file = NotionFile(
                        id=block_id,
                        name=filename,
                        type=block_type,
                        size=0,  # Notion API 不提供大小信息
                        url=url,
                        parent_id=page_id,
                        expiration_time=datetime.now() + timedelta(seconds=settings.PRESIGNED_URL_EXPIRATION)
                    )
                    files.append(file)
                    print(f"  添加文件: {filename}")

            # Recursively check child blocks
            if block.get("has_children", False):
                child_files = await self.get_files_from_block(block_id)
                files.extend(child_files)

        return files

    async def get_files_from_block(self, block_id: str) -> List[NotionFile]:
        """Get all files from a block"""
        files = []

        # Get the block
        try:
            block = self.client.blocks.retrieve(block_id)

            # Check if this is a file block
            block_type = block.get("type")
            if is_file_block(block):
                print(f"\n找到文件块: {block_id}, 类型: {block_type}")

                # 尝试不同的方式提取 URL
                url = ""
                filename = f"file_{block_id}.{block_type}"

                # 方式 1: 直接从块内容中提取 URL
                if block_type in block:
                    block_content = block[block_type]

                    # 检查是否有直接的 URL
                    if isinstance(block_content, dict) and "url" in block_content:
                        url = block_content["url"]
                        print(f"  从块内容直接提取 URL: {url}")

                    # 检查是否有类型字段
                    elif isinstance(block_content, dict) and "type" in block_content:
                        content_type = block_content["type"]
                        if content_type in block_content and "url" in block_content[content_type]:
                            url = block_content[content_type]["url"]
                            print(f"  从内容类型提取 URL: {url}")

                    # 检查是否有文件字段
                    elif isinstance(block_content, dict) and "file" in block_content:
                        if "url" in block_content["file"]:
                            url = block_content["file"]["url"]
                            print(f"  从文件字段提取 URL: {url}")

                    # 检查是否有外部字段
                    elif isinstance(block_content, dict) and "external" in block_content:
                        if "url" in block_content["external"]:
                            url = block_content["external"]["url"]
                            print(f"  从外部字段提取 URL: {url}")

                    # 检查是否有标题或名称
                    if isinstance(block_content, dict):
                        if "title" in block_content:
                            title_parts = block_content["title"]
                            if isinstance(title_parts, list):
                                title = "".join([part.get("plain_text", "") for part in title_parts])
                                if title:
                                    filename = title
                                    print(f"  从标题提取文件名: {filename}")
                        elif "caption" in block_content:
                            caption_parts = block_content["caption"]
                            if isinstance(caption_parts, list):
                                caption = "".join([part.get("plain_text", "") for part in caption_parts])
                                if caption:
                                    filename = caption
                                    print(f"  从标题提取文件名: {filename}")

                # 方式 2: 传统方式提取
                if not url:
                    file_data = block.get(block_type, {})
                    file_type = file_data.get("type", "external")
                    file_info = file_data.get(file_type, {})
                    url = file_info.get("url", "")
                    print(f"  传统方式提取 URL: {url}")

                if url:
                    # 从 URL 提取文件名
                    parsed_url = urllib.parse.urlparse(url)
                    path = parsed_url.path
                    url_filename = os.path.basename(path)

                    # 如果没有从块内容提取到文件名，则使用 URL 中的文件名
                    if filename.startswith("file_"):
                        filename = url_filename

                    # 解码 URL 编码字符
                    filename = decode_url_encoding(filename)
                    print(f"  最终文件名: {filename}")

                    # 创建 NotionFile 对象
                    file = NotionFile(
                        id=block_id,
                        name=filename,
                        type=block_type,
                        size=0,  # Notion API 不提供大小信息
                        url=url,
                        parent_id=block_id,
                        expiration_time=datetime.now() + timedelta(seconds=settings.PRESIGNED_URL_EXPIRATION)
                    )
                    files.append(file)
                    print(f"  添加文件: {filename}")

            # Get child blocks
            if block.get("has_children", False):
                children = await self.get_children(block_id, NotionIdType.BLOCK)

                for child in children:
                    child_id = child.get("id")
                    child_files = await self.get_files_from_block(child_id)
                    files.extend(child_files)

        except Exception as e:
            # Handle errors
            pass

        return files

    async def get_files_from_database(self, database_id: str) -> List[NotionFile]:
        """Get all files from a database"""
        files = []

        # Query the database to get all pages
        pages = await self.get_children(database_id, NotionIdType.DATABASE)

        # Get files from each page
        for page in pages:
            page_id = page.get("id")
            page_files = await self.get_files_from_page(page_id)
            files.extend(page_files)

        return files

    async def get_all_files(self, notion_id: str) -> List[NotionFile]:
        """Get all files from any Notion object"""
        id_type, _ = await self.identify_id_type(notion_id)

        if id_type == NotionIdType.PAGE:
            return await self.get_files_from_page(notion_id)
        elif id_type == NotionIdType.DATABASE:
            return await self.get_files_from_database(notion_id)
        elif id_type == NotionIdType.BLOCK:
            return await self.get_files_from_block(notion_id)
        else:
            return []

    async def create_folder_structure(self, notion_id: str) -> Dict[str, NotionFolder]:
        """
        Create a folder structure based on Notion pages and subpages
        """
        folders = {}

        # Get all subpages
        pages = await self.get_all_subpages_recursive(notion_id)

        # Create root folder
        root_title = pages[notion_id].title if notion_id in pages else "Root"
        root_folder = NotionFolder(
            id=notion_id,
            name=root_title,
            parent_id=None
        )
        folders[notion_id] = root_folder

        # Create folders for each page
        for page_id, page in pages.items():
            if page_id == notion_id:
                continue

            # Try to find the parent
            parent_id = None
            for potential_parent_id, potential_parent in pages.items():
                if potential_parent_id == page_id:
                    continue

                # Check if this page is a child of the potential parent
                children = await self.get_children(potential_parent_id, potential_parent.type)
                for child in children:
                    if child.get("id") == page_id:
                        parent_id = potential_parent_id
                        break

                if parent_id:
                    break

            # Create folder
            folder = NotionFolder(
                id=page_id,
                name=page.title,
                parent_id=parent_id or notion_id
            )
            folders[page_id] = folder

            # Add to parent's children
            if parent_id and parent_id in folders:
                folders[parent_id].children.append(page_id)
            elif notion_id in folders:
                folders[notion_id].children.append(page_id)

        return folders
