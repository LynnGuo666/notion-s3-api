import os
import json
import asyncio
from typing import Dict, List, Optional, Any, Tuple, Set
import urllib.parse
from datetime import datetime, timedelta

from notion_client import Client, AsyncClient
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
        self.async_client = AsyncClient(auth=self.api_key)
        self.cache = {}
        self.cache_expiration = {}

        # 并发请求限制
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

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
        递归获取所有子页面，包括子页面的子页面、数据库子页面等
        """
        if visited is None:
            visited = set()

        if parent_id in visited:
            return {}

        visited.add(parent_id)
        result = {}

        # 识别父类型
        async with self.semaphore:
            id_type, obj_data = await self.identify_id_type(parent_id)

        if id_type == NotionIdType.UNKNOWN:
            return result

        # 获取父标题
        async with self.semaphore:
            title = await self.get_object_title(parent_id)

        # 将父项添加到结果中
        parent_obj = NotionObject(
            id=parent_id,
            type=id_type,
            title=title,
            created_time=datetime.fromisoformat(obj_data.get("created_time", datetime.now().isoformat())),
            last_edited_time=datetime.fromisoformat(obj_data.get("last_edited_time", datetime.now().isoformat())),
            url=obj_data.get("url", "")
        )
        result[parent_id] = parent_obj

        # 获取子项
        async with self.semaphore:
            children = await self.get_children(parent_id, id_type)

        # 并行处理子项
        tasks = []
        for child in children:
            child_id = child.get("id")
            child_type = child.get("type")

            # 如果子项是页面或数据库，递归获取其子页面
            if child_type == "child_page" or child_type == "child_database":
                tasks.append(self.get_all_subpages_recursive(child_id, visited))
            elif id_type == NotionIdType.DATABASE:
                # 数据库查询结果是页面
                tasks.append(self.get_all_subpages_recursive(child_id, visited))

        # 并行执行任务
        if tasks:
            child_results = await asyncio.gather(*tasks)
            for child_result in child_results:
                result.update(child_result)

        return result

    async def get_files_from_page(self, page_id: str) -> List[NotionFile]:
        """从页面获取所有文件"""
        files = []

        # 获取页面中的所有块
        async with self.semaphore:
            blocks = await self.get_children(page_id, NotionIdType.PAGE)

        # 并行处理块
        tasks = []
        for block in blocks:
            block_id = block.get("id")
            block_type = block.get("type")

            # 检查这是否是文件块
            if is_file_block(block):
                tasks.append(self._extract_file_from_block(block, block_id, block_type, page_id))

            # 递归检查子块
            if block.get("has_children", False):
                tasks.append(self.get_files_from_block(block_id))

        # 并行执行任务
        if tasks:
            results = await asyncio.gather(*tasks)
            for result in results:
                if isinstance(result, list):
                    files.extend(result)
                elif result:  # 单个文件
                    files.append(result)

        return files

    async def _extract_file_from_block(self, block: Dict[str, Any], block_id: str, block_type: str, parent_id: str) -> Optional[NotionFile]:
        """从块中提取文件"""
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
                parent_id=parent_id,
                expiration_time=datetime.now() + timedelta(seconds=settings.PRESIGNED_URL_EXPIRATION)
            )
            print(f"  添加文件: {filename}")
            return file

        return None

    async def get_files_from_block(self, block_id: str) -> List[NotionFile]:
        """从块获取所有文件"""
        files = []

        # 获取块
        try:
            async with self.semaphore:
                block = await self.async_client.blocks.retrieve(block_id)

            # 检查这是否是文件块
            block_type = block.get("type")
            if is_file_block(block):
                file = await self._extract_file_from_block(block, block_id, block_type, block_id)
                if file:
                    files.append(file)

            # 获取子块
            if block.get("has_children", False):
                async with self.semaphore:
                    children = await self.get_children(block_id, NotionIdType.BLOCK)

                # 并行处理子块
                tasks = []
                for child in children:
                    child_id = child.get("id")
                    tasks.append(self.get_files_from_block(child_id))

                # 并行执行任务
                if tasks:
                    results = await asyncio.gather(*tasks)
                    for result in results:
                        files.extend(result)

        except Exception as e:
            # 处理错误
            print(f"Error getting files from block {block_id}: {e}")

        return files

    async def get_files_from_database(self, database_id: str) -> List[NotionFile]:
        """从数据库获取所有文件"""
        files = []

        # 查询数据库以获取所有页面
        async with self.semaphore:
            pages = await self.get_children(database_id, NotionIdType.DATABASE)

        # 并行获取每个页面的文件
        tasks = []
        for page in pages:
            page_id = page.get("id")
            tasks.append(self.get_files_from_page(page_id))

        # 并行执行任务
        if tasks:
            results = await asyncio.gather(*tasks)
            for result in results:
                files.extend(result)

        return files

    async def get_all_files(self, notion_id: str) -> List[NotionFile]:
        """从任何 Notion 对象获取所有文件"""
        async with self.semaphore:
            id_type, _ = await self.identify_id_type(notion_id)

        print(f"开始从 {notion_id} ({id_type}) 获取文件")

        all_files = []

        # 获取当前对象的文件
        if id_type == NotionIdType.PAGE:
            files = await self.get_files_from_page(notion_id)
            all_files.extend(files)
        elif id_type == NotionIdType.DATABASE:
            files = await self.get_files_from_database(notion_id)
            all_files.extend(files)
        elif id_type == NotionIdType.BLOCK:
            files = await self.get_files_from_block(notion_id)
            all_files.extend(files)
        else:
            print(f"未知的 Notion ID 类型: {id_type}")
            return []

        # 获取子页面的文件
        try:
            # 获取所有子页面
            notion_objects = await self.get_all_subpages_recursive(notion_id)

            # 并行获取每个子页面的文件
            tasks = []
            for obj_id, obj in notion_objects.items():
                if obj_id != notion_id:  # 跳过当前页面
                    tasks.append(self._get_files_from_object(obj_id, obj.type))

            # 并行执行任务
            if tasks:
                results = await asyncio.gather(*tasks)
                for result in results:
                    all_files.extend(result)
        except Exception as e:
            print(f"获取子页面文件时出错: {e}")

        return all_files

    async def _get_files_from_object(self, obj_id: str, obj_type: NotionIdType) -> List[NotionFile]:
        """从对象获取文件，用于并行处理"""
        try:
            if obj_type == NotionIdType.PAGE:
                return await self.get_files_from_page(obj_id)
            elif obj_type == NotionIdType.DATABASE:
                return await self.get_files_from_database(obj_id)
            elif obj_type == NotionIdType.BLOCK:
                return await self.get_files_from_block(obj_id)
            else:
                return []
        except Exception as e:
            print(f"从对象 {obj_id} 获取文件时出错: {e}")
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
