import os
import json
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import urllib.parse

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.responses import Response  # Use Response instead of XMLResponse
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models import NotionIdType, NotionObject, NotionFile, NotionFolder, S3ListObjectsResponse, S3Error
from notion_api_client import NotionAPI
from s3_adapter import S3Adapter
from utils import detect_notion_id_type, decode_url_encoding, format_datetime_for_browser, get_browser_timezone, convert_to_browser_timezone

app = FastAPI(title="Notion S3 API", description="用于 Notion 内容的 S3 兼容 API")

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化 Notion API 客户端
notion_api = NotionAPI()

# 初始化 S3 适配器
s3_adapter = S3Adapter()

# 内存中缓存 Notion ID
notion_id_cache = {}


async def get_notion_id_from_request(request: Request) -> str:
    """从请求中获取 Notion ID（查询参数或会话）"""
    # 检查查询参数
    notion_id = request.query_params.get("id")

    if not notion_id:
        # 检查会话或使用默认值
        # 在实际实现中，这将使用会话
        notion_id = notion_id_cache.get("current_id")

    if not notion_id:
        raise HTTPException(status_code=400, detail="需要提供 Notion ID")

    # 存储在缓存中供将来请求使用
    notion_id_cache["current_id"] = notion_id

    return notion_id


@app.get("/")
async def root():
    """根端点 - 重定向到文档"""
    return {"message": "Notion S3 API", "docs_url": "/docs"}


@app.get("/api/notion/id")
async def set_notion_id(id: str):
    """设置用于后续请求的 Notion ID"""
    # 验证 ID
    id_type, formatted_id = detect_notion_id_type(id)

    if id_type == NotionIdType.UNKNOWN:
        # 尝试识别类型
        id_type, _ = await notion_api.identify_id_type(formatted_id)

    if id_type == NotionIdType.UNKNOWN:
        raise HTTPException(status_code=400, detail="无效的 Notion ID")

    # 存储在缓存中
    notion_id_cache["current_id"] = formatted_id

    return {
        "id": formatted_id,
        "type": id_type,
        "message": f"Notion ID 设置为 {formatted_id} (类型: {id_type})"
    }


@app.get("/api/notion/refresh")
async def refresh_notion_data(notion_id: str = Depends(get_notion_id_from_request)):
    """刷新 Notion 数据"""
    try:
        # 识别 ID 类型
        id_type, _ = await notion_api.identify_id_type(notion_id)

        if id_type == NotionIdType.UNKNOWN:
            raise HTTPException(status_code=400, detail="无效的 Notion ID")

        # 获取所有子页面
        notion_objects = await notion_api.get_all_subpages_recursive(notion_id)

        # 创建文件夹结构
        notion_folders = await notion_api.create_folder_structure(notion_id)

        # 获取所有文件
        notion_files = await notion_api.get_all_files(notion_id)

        # 更新 S3 适配器
        await s3_adapter.update_from_notion_data(notion_objects, notion_folders, notion_files)

        return {
            "message": "Notion 数据刷新成功",
            "objects_count": len(notion_objects),
            "folders_count": len(notion_folders),
            "files_count": len(notion_files)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新 Notion 数据时出错: {str(e)}")


# S3 兼容 API 端点

@app.get("/{bucket}")
async def list_bucket_objects(
    bucket: str,
    prefix: Optional[str] = Query("", alias="prefix"),
    delimiter: Optional[str] = Query("", alias="delimiter"),
    marker: Optional[str] = Query("", alias="marker"),
    max_keys: Optional[int] = Query(1000, alias="max-keys"),
    notion_id: str = Depends(get_notion_id_from_request)
):
    """列出存储桶中的对象（S3 兼容）"""
    if bucket != settings.S3_BUCKET_NAME:
        # Return error in XML format
        error = S3Error(
            Code="NoSuchBucket",
            Message=f"The specified bucket {bucket} does not exist",
            Resource=f"/{bucket}",
            RequestId="notion-s3-api"
        )

        root = ET.Element("Error")
        for key, value in error.dict().items():
            child = ET.SubElement(root, key)
            child.text = str(value)

        xml_str = ET.tostring(root, encoding="utf-8", method="xml")
        return Response(content=xml_str, media_type="application/xml", status_code=404)

    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    # List objects
    response = await s3_adapter.list_objects(prefix, delimiter, max_keys)

    # Convert to XML
    root = ET.Element("ListBucketResult")
    ET.SubElement(root, "Name").text = response.Name
    ET.SubElement(root, "Prefix").text = response.Prefix
    ET.SubElement(root, "Marker").text = response.Marker
    ET.SubElement(root, "MaxKeys").text = str(response.MaxKeys)
    ET.SubElement(root, "IsTruncated").text = str(response.IsTruncated).lower()

    for obj in response.Contents:
        content = ET.SubElement(root, "Contents")
        ET.SubElement(content, "Key").text = obj.Key
        ET.SubElement(content, "LastModified").text = obj.LastModified.isoformat()
        ET.SubElement(content, "ETag").text = obj.ETag
        ET.SubElement(content, "Size").text = str(obj.Size)
        ET.SubElement(content, "StorageClass").text = obj.StorageClass

        owner = ET.SubElement(content, "Owner")
        ET.SubElement(owner, "DisplayName").text = obj.Owner["DisplayName"]

    xml_str = ET.tostring(root, encoding="utf-8", method="xml")
    return Response(content=xml_str, media_type="application/xml")


@app.get("/{bucket}/{key:path}")
async def get_object(
    bucket: str,
    key: str,
    notion_id: str = Depends(get_notion_id_from_request)
):
    """从存储桶获取对象（S3 兼容）"""
    if bucket != settings.S3_BUCKET_NAME:
        # Return error in XML format
        error = S3Error(
            Code="NoSuchBucket",
            Message=f"The specified bucket {bucket} does not exist",
            Resource=f"/{bucket}/{key}",
            RequestId="notion-s3-api"
        )

        root = ET.Element("Error")
        for key, value in error.dict().items():
            child = ET.SubElement(root, key)
            child.text = str(value)

        xml_str = ET.tostring(root, encoding="utf-8", method="xml")
        return Response(content=xml_str, media_type="application/xml", status_code=404)

    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    # Get object
    obj = await s3_adapter.get_object(key)

    if not obj:
        # Return error in XML format
        error = S3Error(
            Code="NoSuchKey",
            Message=f"The specified key {key} does not exist",
            Resource=f"/{bucket}/{key}",
            RequestId="notion-s3-api"
        )

        root = ET.Element("Error")
        for k, value in error.dict().items():
            child = ET.SubElement(root, k)
            child.text = str(value)

        xml_str = ET.tostring(root, encoding="utf-8", method="xml")
        return Response(content=xml_str, media_type="application/xml", status_code=404)

    # Generate presigned URL
    url = await s3_adapter.generate_presigned_url(key)

    if url:
        # Redirect to the URL
        return RedirectResponse(url)
    else:
        # Return error
        raise HTTPException(status_code=404, detail=f"Object not found: {key}")


@app.get("/api/files")
async def list_files(notion_id: str = Depends(get_notion_id_from_request)):
    """列出所有文件"""
    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    files = []

    for file_id, file_data in s3_adapter.files.items():
        file = NotionFile(**file_data)

        # Find parent folder
        parent_id = file.parent_id
        parent_name = "Root"

        if parent_id in s3_adapter.folders:
            folder = NotionFolder(**s3_adapter.folders[parent_id])
            parent_name = folder.name

        # Get expiration time in browser timezone
        expiration_time = file.expiration_time
        if expiration_time:
            browser_timezone = get_browser_timezone()
            expiration_time = convert_to_browser_timezone(expiration_time, browser_timezone)
            expiration_str = format_datetime_for_browser(expiration_time)
        else:
            expiration_str = None

        files.append({
            "id": file.id,
            "name": file.name,
            "type": file.type,
            "size": file.size,
            "url": file.url,
            "parent_id": parent_id,
            "parent_name": parent_name,
            "expiration_time": expiration_str
        })

    return {"files": files}


@app.get("/api/folders")
async def list_folders(notion_id: str = Depends(get_notion_id_from_request)):
    """列出所有文件夹"""
    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    folders = []

    for folder_id, folder_data in s3_adapter.folders.items():
        folder = NotionFolder(**folder_data)

        # Find parent folder
        parent_id = folder.parent_id
        parent_name = "Root"

        if parent_id and parent_id in s3_adapter.folders:
            parent_folder = NotionFolder(**s3_adapter.folders[parent_id])
            parent_name = parent_folder.name

        folders.append({
            "id": folder.id,
            "name": folder.name,
            "parent_id": parent_id,
            "parent_name": parent_name,
            "children_count": len(folder.children)
        })

    return {"folders": folders}


@app.get("/api/file/{file_id}")
async def get_file_info(file_id: str, notion_id: str = Depends(get_notion_id_from_request)):
    """获取文件信息"""
    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    if file_id not in s3_adapter.files:
        raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

    file_data = s3_adapter.files[file_id]
    file = NotionFile(**file_data)

    # Find parent folder
    parent_id = file.parent_id
    parent_name = "Root"

    if parent_id in s3_adapter.folders:
        folder = NotionFolder(**s3_adapter.folders[parent_id])
        parent_name = folder.name

    # Get expiration time in browser timezone
    expiration_time = file.expiration_time
    if expiration_time:
        browser_timezone = get_browser_timezone()
        expiration_time = convert_to_browser_timezone(expiration_time, browser_timezone)
        expiration_str = format_datetime_for_browser(expiration_time)
    else:
        expiration_str = None

    return {
        "id": file.id,
        "name": file.name,
        "type": file.type,
        "size": file.size,
        "url": file.url,
        "parent_id": parent_id,
        "parent_name": parent_name,
        "expiration_time": expiration_str
    }


@app.get("/api/folder/{folder_id}")
async def get_folder_info(folder_id: str, notion_id: str = Depends(get_notion_id_from_request)):
    """获取文件夹信息"""
    # Refresh data if needed
    if not s3_adapter.objects:
        await refresh_notion_data(notion_id)

    if folder_id not in s3_adapter.folders:
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder_id}")

    folder_data = s3_adapter.folders[folder_id]
    folder = NotionFolder(**folder_data)

    # Find parent folder
    parent_id = folder.parent_id
    parent_name = "Root"

    if parent_id and parent_id in s3_adapter.folders:
        parent_folder = NotionFolder(**s3_adapter.folders[parent_id])
        parent_name = parent_folder.name

    # Get children
    children = []

    for child_id in folder.children:
        if child_id in s3_adapter.folders:
            child_folder = NotionFolder(**s3_adapter.folders[child_id])
            children.append({
                "id": child_id,
                "name": child_folder.name,
                "type": "folder"
            })

    # Get files in this folder
    files = []

    for file_id, file_data in s3_adapter.files.items():
        file = NotionFile(**file_data)

        if file.parent_id == folder_id:
            files.append({
                "id": file_id,
                "name": file.name,
                "type": "file",
                "file_type": file.type,
                "size": file.size,
                "url": file.url
            })

    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": parent_id,
        "parent_name": parent_name,
        "children": children,
        "files": files
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
