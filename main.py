import os
import json
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import urllib.parse

from fastapi import FastAPI, HTTPException, Query, Request, Response, Header, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from auth import s3_auth_required

from config import settings
from models import NotionIdType, NotionObject, NotionFile, NotionFolder, S3ListObjectsResponse, S3Error
from notion_api_client import NotionAPI
from s3_adapter import S3Adapter
from utils import detect_notion_id_type, decode_url_encoding, format_datetime_for_browser

app = FastAPI(
    title="Notion S3 API",
    description="用于 Notion 内容的 S3 兼容 API",
    version=settings.VERSION
)

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

# API 密钥验证
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Depends(api_key_header)):
    """验证 API 密钥"""
    if not settings.API_KEY or api_key == settings.API_KEY:
        return api_key
    raise HTTPException(
        status_code=403,
        detail="无效的 API 密钥"
    )


async def get_notion_id_from_request(request: Request) -> str:
    """从请求中获取 Notion ID"""
    notion_id = request.query_params.get("id")

    if not notion_id:
        raise HTTPException(status_code=400, detail="需要提供 Notion ID")

    return notion_id


@app.get("/")
async def root():
    """根端点 - 重定向到文档"""
    return {"message": "Notion S3 API", "docs_url": "/docs"}


async def process_notion_data(notion_id: str):
    """处理 Notion 数据并更新 S3 适配器"""
    try:
        print(f"处理 Notion ID: {notion_id}")

        # 识别 ID 类型并格式化
        id_type_initial, formatted_id = detect_notion_id_type(notion_id)
        print(f"初始 ID 类型: {id_type_initial}, 格式化后的 ID: {formatted_id}")

        # 尝试识别 ID 类型
        id_type, obj_data = await notion_api.identify_id_type(formatted_id)
        print(f"识别后的 ID 类型: {id_type}")

        if id_type == NotionIdType.UNKNOWN:
            # 尝试直接使用原始 ID
            print(f"尝试使用原始 ID: {notion_id}")
            id_type, obj_data = await notion_api.identify_id_type(notion_id)
            print(f"使用原始 ID 识别后的类型: {id_type}")

            if id_type == NotionIdType.UNKNOWN:
                raise HTTPException(status_code=400, detail=f"无效的 Notion ID: {notion_id}")
            else:
                # 使用原始 ID
                formatted_id = notion_id

        # 获取所有子页面
        print(f"获取子页面: {formatted_id}")
        notion_objects = await notion_api.get_all_subpages_recursive(formatted_id)
        print(f"找到 {len(notion_objects)} 个对象")

        # 创建文件夹结构
        print(f"创建文件夹结构")
        notion_folders = await notion_api.create_folder_structure(formatted_id)
        print(f"创建了 {len(notion_folders)} 个文件夹")

        # 获取所有文件
        print(f"获取文件")
        notion_files = await notion_api.get_all_files(formatted_id)
        print(f"找到 {len(notion_files)} 个文件")

        # 更新 S3 适配器
        print(f"更新 S3 适配器")
        await s3_adapter.update_from_notion_data(notion_objects, notion_folders, notion_files)

        return {
            "id": formatted_id,
            "type": id_type,
            "objects_count": len(notion_objects),
            "folders_count": len(notion_folders),
            "files_count": len(notion_files)
        }
    except HTTPException:
        # 重新抛出 HTTP 异常
        raise
    except Exception as e:
        import traceback
        print(f"处理 Notion 数据时出错: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"处理 Notion 数据时出错: {str(e)}")


# API 端点

@app.get("/api/{notion_id}", dependencies=[Depends(verify_api_key)])
async def get_notion_content(notion_id: str):
    """获取 Notion 内容并返回 API 格式的下载链接"""
    # 处理 Notion 数据
    result = await process_notion_data(notion_id)

    # 获取所有文件
    files = []
    for file_id, file_data in s3_adapter.files.items():
        file = NotionFile(**file_data)

        # 找到父文件夹路径
        path = ""
        parent_id = file.parent_id

        while parent_id and parent_id in s3_adapter.folders:
            folder = NotionFolder(**s3_adapter.folders[parent_id])
            path = f"{folder.name}/{path}"
            parent_id = folder.parent_id

        # 添加过期时间
        expiration_time = file.expiration_time
        expiration_str = format_datetime_for_browser(expiration_time) if expiration_time else None

        files.append({
            "id": file.id,
            "name": file.name,
            "path": path + file.name,
            "type": file.type,
            "size": file.size,
            "url": file.url,
            "expiration_time": expiration_str
        })

    return {
        "id": result["id"],
        "type": result["type"],
        "files_count": len(files),
        "files": files
    }


# S3 兼容 API 端点

@app.get("/{bucket}", dependencies=[Depends(s3_auth_required)])
async def list_bucket_objects(
    bucket: str,
    prefix: Optional[str] = Query("", alias="prefix"),
    delimiter: Optional[str] = Query("", alias="delimiter"),
    max_keys: Optional[int] = Query(1000, alias="max-keys")
):
    """列出存储桶中的对象（S3 兼容）"""
    # 处理 Notion 数据（存储桶名称就是 Notion ID）
    try:
        await process_notion_data(bucket)
    except Exception as e:
        # 返回 S3 格式的错误
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

    # 列出对象
    response = await s3_adapter.list_objects(bucket, prefix, delimiter, max_keys)

    # 转换为 XML
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


@app.get("/{bucket}/{key:path}", dependencies=[Depends(s3_auth_required)])
async def get_object(
    bucket: str,
    key: str
):
    """从存储桶获取对象（S3 兼容）"""
    # 处理 Notion 数据（存储桶名称就是 Notion ID）
    try:
        await process_notion_data(bucket)
    except Exception as e:
        # 返回 S3 格式的错误
        error = S3Error(
            Code="NoSuchBucket",
            Message=f"The specified bucket {bucket} does not exist",
            Resource=f"/{bucket}/{key}",
            RequestId="notion-s3-api"
        )

        root = ET.Element("Error")
        for k, value in error.dict().items():
            child = ET.SubElement(root, k)
            child.text = str(value)

        xml_str = ET.tostring(root, encoding="utf-8", method="xml")
        return Response(content=xml_str, media_type="application/xml", status_code=404)

    # 获取对象
    obj = await s3_adapter.get_object(key)

    if not obj:
        # 返回 S3 格式的错误
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

    # 生成预签名 URL
    url = await s3_adapter.generate_presigned_url(key)

    if url:
        # 重定向到 URL
        return RedirectResponse(url)
    else:
        # 返回错误
        raise HTTPException(status_code=404, detail=f"找不到对象: {key}")


if __name__ == "__main__":
    import uvicorn
    print(f"\n启动 Notion S3 API 服务器...")
    print(f"API 将在 http://{settings.API_HOST if settings.API_HOST != '0.0.0.0' else 'localhost'}:{settings.API_PORT} 可用")
    print(f"\n使用方法：")
    print(f"1. API 格式获取文件链接： GET /api/你的_notion_id")
    print(f"2. S3 兼容格式获取文件列表： GET /你的_notion_id")
    print(f"3. S3 兼容格式获取文件： GET /你的_notion_id/文件路径\n")
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)
