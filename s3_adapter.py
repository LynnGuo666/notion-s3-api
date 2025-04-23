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
        """Update the S3 adapter with data from Notion"""
        # Clear existing data
        self.objects = {}
        self.folders = {}
        self.files = {}

        # Add objects
        for obj_id, obj in notion_objects.items():
            self.objects[obj_id] = obj.dict()

        # Add folders
        for folder_id, folder in notion_folders.items():
            self.folders[folder_id] = folder.dict()

            # Create S3 object for folder
            s3_obj = self._get_s3_object_from_notion_folder(folder)
            key = s3_obj.Key

            if key not in self.objects:
                self.objects[key] = s3_obj.dict()

        # Add files
        for file in notion_files:
            file_id = file.id
            self.files[file_id] = file.dict()

            # Find parent folder
            parent_id = file.parent_id
            prefix = ""

            if parent_id in self.folders:
                folder = NotionFolder(**self.folders[parent_id])
                prefix = generate_s3_key(folder.id, "", folder.name + "/")

            # Create S3 object for file
            s3_obj = self._get_s3_object_from_notion_file(file, prefix)
            key = s3_obj.Key

            if key not in self.objects:
                self.objects[key] = s3_obj.dict()

    async def list_objects(self, bucket_name: str, prefix: str = "", delimiter: str = "", max_keys: int = 1000) -> S3ListObjectsResponse:
        """列出 S3 存储桶中的对象"""
        contents = []

        # Filter objects by prefix
        filtered_objects = {
            key: obj for key, obj in self.objects.items()
            if key.startswith(prefix)
        }

        # Handle delimiter (for directory-like listing)
        if delimiter:
            # Group by common prefixes
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

            # Add common prefixes as directory objects
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
                    print(f"Converting object to S3 format: {key}")
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
            # No delimiter, just list all objects with the prefix
            for key, obj in filtered_objects.items():
                # 检查对象是否有所需的 S3 字段
                if isinstance(obj, dict) and "Key" in obj and "LastModified" in obj and "ETag" in obj and "Size" in obj:
                    # 已经是 S3 格式
                    s3_obj = S3Object(**obj)
                else:
                    # 需要转换为 S3 格式
                    print(f"Converting object to S3 format: {key}")
                    s3_obj = S3Object(
                        Key=key,
                        LastModified=datetime.now(),
                        ETag=f'"{generate_etag(key)}"',
                        Size=obj.get("size", 0) if isinstance(obj, dict) else 0,
                        StorageClass="STANDARD",
                        Owner={"DisplayName": "notion-s3-api"}
                    )
                contents.append(s3_obj)

        # Sort by key
        contents.sort(key=lambda x: x.Key)

        # Apply max_keys
        if max_keys > 0 and len(contents) > max_keys:
            contents = contents[:max_keys]
            is_truncated = True
        else:
            is_truncated = False

        return S3ListObjectsResponse(
            Name=bucket_name,
            Prefix=prefix,
            Marker="",
            MaxKeys=max_keys,
            IsTruncated=is_truncated,
            Contents=contents
        )

    async def get_object(self, key: str) -> Optional[Dict[str, Any]]:
        """Get an object from the S3 bucket"""
        if key in self.objects:
            return self.objects[key]

        # Check if this is a file
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
                return {
                    "Body": None,  # We don't store the actual file content
                    "ContentType": "application/octet-stream",
                    "ContentLength": file_obj.size or 0,
                    "ETag": f'"{generate_etag(file_id)}"',
                    "LastModified": datetime.now(),
                    "Metadata": {
                        "notion_id": file_id,
                        "notion_url": file_obj.url
                    }
                }

        return None

    async def generate_presigned_url(self, key: str) -> Optional[str]:
        """Generate a presigned URL for an object"""
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
                # Return the Notion URL
                return file_obj.url

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
