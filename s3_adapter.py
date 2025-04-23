import os
import json
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import urllib.parse
import hashlib

from models import NotionObject, NotionFile, NotionFolder, S3Object, S3ListObjectsResponse
from config import settings
from utils import generate_etag, format_datetime_for_browser, generate_s3_key, parse_s3_key


class S3Adapter:
    def __init__(self):
        self.presigned_url_expiration = settings.PRESIGNED_URL_EXPIRATION

        # 内存存储对象
        self.objects: Dict[str, Dict[str, Any]] = {}
        self.folders: Dict[str, Dict[str, Any]] = {}
        self.files: Dict[str, Dict[str, Any]] = {}

        # 缓存
        self.cache = {}

    def log(self, message, is_step=False, is_success=False, is_error=False, indent=0):
        """输出日志"""
        prefix = "  " * indent
        if is_step:
            prefix += "\033[1;34m[S3]\033[0m "
        elif is_success:
            prefix += "\033[1;32m[S3_OK]\033[0m "
        elif is_error:
            prefix += "\033[1;31m[S3_ERROR]\033[0m "
        else:
            prefix += "\033[1;36m[S3_INFO]\033[0m "

        print(f"{prefix}{message}")

        # 缓存
        self.cache = {}

    def _get_s3_object_from_notion_file(self, file: NotionFile, prefix: str = "") -> S3Object:
        """Convert a NotionFile to an S3Object"""
        key = generate_s3_key(file.id, prefix, file.name)

        return S3Object(
            Key=key,
            LastModified=datetime.now(),
            ETag=f'"{generate_etag(file.id)}"',
            Size=file.size or 0,
            StorageClass="STANDARD",
            Owner={"DisplayName": "notion-s3-api"}
        )

    def _get_s3_object_from_notion_folder(self, folder: NotionFolder, prefix: str = "") -> S3Object:
        """Convert a NotionFolder to an S3Object (as a directory)"""
        key = generate_s3_key(folder.id, prefix, folder.name + "/")

        return S3Object(
            Key=key,
            LastModified=datetime.now(),
            ETag=f'"{generate_etag(folder.id)}"',
            Size=0,  # Directories have size 0
            StorageClass="STANDARD",
            Owner={"DisplayName": "notion-s3-api"}
        )

    async def update_from_notion_data(
        self,
        notion_objects: Dict[str, NotionObject],
        notion_folders: Dict[str, NotionFolder],
        notion_files: List[NotionFile]
    ) -> None:
        """从 Notion 数据更新 S3 适配器"""
        self.log("\n开始更新 S3 适配器...", is_step=True)
        start_time = datetime.now()

        # 清除现有数据
        self.objects = {}
        self.folders = {}
        self.files = {}
        self.cache = {}  # 清除缓存

        # 添加对象
        self.log(f"添加 {len(notion_objects)} 个 Notion 对象", is_step=True)
        for obj_id, obj in notion_objects.items():
            self.objects[obj_id] = obj.dict()

        # 添加文件夹
        self.log(f"添加 {len(notion_folders)} 个文件夹", is_step=True)
        for folder_id, folder in notion_folders.items():
            self.folders[folder_id] = folder.dict()

            # 为文件夹创建 S3 对象
            s3_obj = self._get_s3_object_from_notion_folder(folder)
            key = s3_obj.Key

            if key not in self.objects:
                self.objects[key] = s3_obj.dict()

        # 添加文件
        self.log(f"添加 {len(notion_files)} 个文件", is_step=True)
        for i, file in enumerate(notion_files):
            if i % 10 == 0 and i > 0:
                self.log(f"已处理 {i}/{len(notion_files)} 个文件", indent=1)

            file_id = file.id
            self.files[file_id] = file.dict()

            # 找到父文件夹
            parent_id = file.parent_id
            prefix = ""

            if parent_id in self.folders:
                folder = NotionFolder(**self.folders[parent_id])
                prefix = generate_s3_key(folder.id, "", folder.name + "/")

            # 为文件创建 S3 对象
            s3_obj = self._get_s3_object_from_notion_file(file, prefix)
            key = s3_obj.Key

            if key not in self.objects:
                self.objects[key] = s3_obj.dict()

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        self.log(f"S3 适配器更新完成，耗时 {duration:.2f} 秒", is_success=True)

    async def list_objects(self, bucket_name: str, prefix: str = "", delimiter: str = "", max_keys: int = 1000) -> S3ListObjectsResponse:
        """列出 S3 存储桶中的对象"""
        self.log(f"\n列出存储桶 {bucket_name} 中的对象，前缀: {prefix}", is_step=True)
        start_time = datetime.now()

        # 使用缓存提高性能
        cache_key = f"list_objects_{bucket_name}_{prefix}_{delimiter}_{max_keys}"
        if cache_key in self.cache:
            self.log(f"使用缓存结果", is_success=True)
            return self.cache[cache_key]

        contents = []

        # 按前缀过滤对象
        filtered_objects = {
            key: obj for key, obj in self.objects.items()
            if key.startswith(prefix)
        }

        self.log(f"找到 {len(filtered_objects)} 个匹配前缀 '{prefix}' 的对象", indent=1)

        # 处理分隔符（用于目录式列表）
        if delimiter:
            self.log(f"使用分隔符: '{delimiter}'", indent=1)
            # 按公共前缀分组
            common_prefixes = set()
            filtered_keys = []

            for key in filtered_objects.keys():
                if key.startswith(prefix):
                    suffix = key[len(prefix):]
                    delimiter_pos = suffix.find(delimiter)

                    if delimiter_pos >= 0:
                        # This is a common prefix
                        common_prefix = prefix + suffix[:delimiter_pos + 1]
                        common_prefixes.add(common_prefix)
                    else:
                        # This is a direct child
                        filtered_keys.append(key)

            self.log(f"找到 {len(common_prefixes)} 个公共前缀和 {len(filtered_keys)} 个直接子项", indent=1)

            # 添加公共前缀作为目录对象
            for common_prefix in common_prefixes:
                s3_obj = S3Object(
                    Key=common_prefix,
                    LastModified=datetime.now(),
                    ETag=f'"{generate_etag(common_prefix)}"',
                    Size=0,
                    StorageClass="STANDARD",
                    Owner={"DisplayName": "notion-s3-api"}
                )
                contents.append(s3_obj)

            # Add direct children
            for key in filtered_keys:
                obj = filtered_objects[key]
                # 检查对象是否有所需的 S3 字段
                if isinstance(obj, dict) and "Key" in obj and "LastModified" in obj and "ETag" in obj and "Size" in obj:
                    # 已经是 S3 格式
                    s3_obj = S3Object(**obj)
                else:
                    # 需要转换为 S3 格式
                    self.log(f"将对象转换为 S3 格式: {key}", indent=2)
                    s3_obj = S3Object(
                        Key=key,
                        LastModified=datetime.now(),
                        ETag=f'"{generate_etag(key)}"',
                        Size=obj.get("size", 0) if isinstance(obj, dict) else 0,
                        StorageClass="STANDARD",
                        Owner={"DisplayName": "notion-s3-api"}
                    )
                contents.append(s3_obj)
        else:
            # 没有分隔符，直接列出所有带前缀的对象
            self.log(f"列出所有带前缀的对象", indent=1)
            for key, obj in filtered_objects.items():
                # 检查对象是否有所需的 S3 字段
                if isinstance(obj, dict) and "Key" in obj and "LastModified" in obj and "ETag" in obj and "Size" in obj:
                    # 已经是 S3 格式
                    s3_obj = S3Object(**obj)
                else:
                    # 需要转换为 S3 格式
                    self.log(f"将对象转换为 S3 格式: {key}", indent=2)
                    s3_obj = S3Object(
                        Key=key,
                        LastModified=datetime.now(),
                        ETag=f'"{generate_etag(key)}"',
                        Size=obj.get("size", 0) if isinstance(obj, dict) else 0,
                        StorageClass="STANDARD",
                        Owner={"DisplayName": "notion-s3-api"}
                    )
                contents.append(s3_obj)

        # 按键排序
        contents.sort(key=lambda x: x.Key)
        self.log(f"排序后的对象数量: {len(contents)}", indent=1)

        # 应用 max_keys
        if max_keys > 0 and len(contents) > max_keys:
            contents = contents[:max_keys]
            is_truncated = True
            self.log(f"应用 max_keys={max_keys}，结果被截断", indent=1)
        else:
            is_truncated = False

        response = S3ListObjectsResponse(
            Name=bucket_name,
            Prefix=prefix,
            Marker="",
            MaxKeys=max_keys,
            IsTruncated=is_truncated,
            Contents=contents,
            CommonPrefixes=[]  # 在main.py中填充
        )

        # 更新缓存
        self.cache[cache_key] = response

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        self.log(f"S3 列表操作完成，耗时 {duration:.2f} 秒", is_success=True)

        return response

    async def get_object(self, key: str) -> Optional[Dict[str, Any]]:
        """从 S3 存储桶获取对象"""
        self.log(f"\n获取对象: {key}", is_step=True)
        start_time = datetime.now()

        # 使用缓存提高性能
        cache_key = f"get_object_{key}"
        if cache_key in self.cache:
            self.log(f"使用缓存结果", is_success=True)
            return self.cache[cache_key]

        if key in self.objects:
            self.log(f"在对象字典中找到对象", is_success=True)
            self.cache[cache_key] = self.objects[key]
            return self.objects[key]

        # 检查这是否是文件
        self.log(f"在文件列表中搜索对象", indent=1)
        for file_id, file in self.files.items():
            file_obj = NotionFile(**file)

            # 找到父文件夹
            parent_id = file_obj.parent_id
            prefix = ""

            if parent_id in self.folders:
                folder = NotionFolder(**self.folders[parent_id])
                prefix = generate_s3_key(folder.id, "", folder.name + "/")

            file_key = generate_s3_key(file_id, prefix, file_obj.name)

            if file_key == key:
                self.log(f"找到文件: {file_obj.name}", is_success=True)
                result = {
                    "Body": None,  # 我们不存储实际的文件内容
                    "ContentType": "application/octet-stream",
                    "ContentLength": file_obj.size or 0,
                    "ETag": f'"{generate_etag(file_id)}"',
                    "LastModified": datetime.now(),
                    "Metadata": {
                        "notion_id": file_id,
                        "notion_url": file_obj.url
                    }
                }
                self.cache[cache_key] = result

                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                self.log(f"S3 获取对象操作完成，耗时 {duration:.2f} 秒", is_success=True)
                return result

        self.log(f"未找到对象: {key}", is_error=True)
        return None

    async def generate_presigned_url(self, key: str) -> Optional[str]:
        """为对象生成预签名 URL"""
        self.log(f"\n生成预签名 URL: {key}", is_step=True)
        start_time = datetime.now()

        # 使用缓存提高性能
        cache_key = f"presigned_url_{key}"
        if cache_key in self.cache:
            self.log(f"使用缓存的 URL", is_success=True)
            return self.cache[cache_key]

        # 找到文件
        self.log(f"在文件列表中搜索对象", indent=1)
        for file_id, file in self.files.items():
            file_obj = NotionFile(**file)

            # 找到父文件夹
            parent_id = file_obj.parent_id
            prefix = ""

            if parent_id in self.folders:
                folder = NotionFolder(**self.folders[parent_id])
                prefix = generate_s3_key(folder.id, "", folder.name + "/")

            file_key = generate_s3_key(file_id, prefix, file_obj.name)

            if file_key == key:
                self.log(f"找到文件: {file_obj.name}", is_success=True)
                self.cache[cache_key] = file_obj.url

                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                self.log(f"S3 生成预签名 URL 操作完成，耗时 {duration:.2f} 秒", is_success=True)
                return file_obj.url

        self.log(f"未找到对象: {key}", is_error=True)
        return None

    def get_expiration_time(self, key: str) -> Optional[datetime]:
        """Get the expiration time for a presigned URL"""
        # Find the file
        for file_id, file in self.files.items():
            file_obj = NotionFile(**file)

            # Find parent folder
            parent_id = file_obj.parent_id
            prefix = ""

            if parent_id in self.folders:
                folder = NotionFolder(**self.folders[parent_id])
                prefix = generate_s3_key(folder.id, "", folder.name + "/")

            file_key = generate_s3_key(file_id, prefix, file_obj.name)

            if file_key == key:
                # Return the expiration time
                return file_obj.expiration_time or (datetime.now() + timedelta(seconds=self.presigned_url_expiration))

        return None
