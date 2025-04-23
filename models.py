from enum import Enum
from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel
from datetime import datetime


class NotionIdType(str, Enum):
    """表示 Notion ID 类型的枚举"""
    PAGE = "page"  # 页面
    BLOCK = "block"  # 块
    DATABASE = "database"  # 数据库
    UNKNOWN = "unknown"  # 未知


class NotionObject(BaseModel):
    """表示 Notion 对象的模型"""
    id: str  # 对象 ID
    type: NotionIdType  # 对象类型
    title: str  # 对象标题
    parent_id: Optional[str] = None  # 父对象 ID
    created_time: Optional[datetime] = None  # 创建时间
    last_edited_time: Optional[datetime] = None  # 最后编辑时间
    url: Optional[str] = None  # 对象 URL


class NotionFile(BaseModel):
    """表示 Notion 文件的模型"""
    id: str  # 文件 ID
    name: str  # 文件名称
    type: str  # 文件类型
    size: int  # 文件大小
    url: str  # 文件 URL
    expiration_time: Optional[datetime] = None  # URL 过期时间
    parent_id: str  # 父对象 ID


class NotionFolder(BaseModel):
    """表示 Notion 文件夹的模型"""
    id: str  # 文件夹 ID
    name: str  # 文件夹名称
    parent_id: Optional[str] = None  # 父文件夹 ID
    children: List[Union[str, 'NotionFolder']] = []  # 子文件夹列表


class S3Object(BaseModel):
    """表示 S3 对象的模型"""
    Key: str  # 对象键名
    LastModified: datetime  # 最后修改时间
    ETag: str  # 实体标签
    Size: int  # 大小
    StorageClass: str = "STANDARD"  # 存储类别
    Owner: Dict[str, str] = {"DisplayName": "notion-s3-api"}  # 所有者信息


class S3ListObjectsResponse(BaseModel):
    """表示 S3 列出对象响应的模型"""
    Name: str  # 存储桶名称
    Prefix: str  # 前缀
    Marker: str  # 标记
    MaxKeys: int  # 最大键数
    IsTruncated: bool  # 是否被截断
    Contents: List[S3Object]  # 内容列表


class S3Error(BaseModel):
    """表示 S3 错误的模型"""
    Code: str  # 错误代码
    Message: str  # 错误消息
    Resource: str  # 资源
    RequestId: str  # 请求 ID
