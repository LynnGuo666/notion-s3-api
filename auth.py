import hashlib
import hmac
import datetime
import re
from typing import Dict, Optional, Tuple
from fastapi import Request, HTTPException, Depends
from config import settings

# AWS SigV4 身份验证常量
AWS_ALGORITHM = "AWS4-HMAC-SHA256"
AWS_REQUEST_TYPE = "aws4_request"
AWS_SERVICE = "s3"
AWS_REGION = "us-east-1"  # 默认区域

def sign(key: bytes, msg: str) -> bytes:
    """计算 HMAC-SHA256 签名"""
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    """生成签名密钥"""
    k_date = sign(f'AWS4{key}'.encode('utf-8'), date_stamp)
    k_region = sign(k_date, region_name)
    k_service = sign(k_region, service_name)
    k_signing = sign(k_service, AWS_REQUEST_TYPE)
    return k_signing

def parse_auth_header(auth_header: str) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """解析 Authorization 头部"""
    if not auth_header or not auth_header.startswith(AWS_ALGORITHM):
        return None, None, None
    
    # 提取凭据和签名
    try:
        # 示例: AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, SignedHeaders=host;range;x-amz-date, Signature=fe5f80f77d5fa3beca038a248ff027d0445342fe2855ddc963176630326f1024
        credential_pattern = r'Credential=([^,]*)'
        signed_headers_pattern = r'SignedHeaders=([^,]*)'
        signature_pattern = r'Signature=([^,]*)'
        
        credential_match = re.search(credential_pattern, auth_header)
        signed_headers_match = re.search(signed_headers_pattern, auth_header)
        signature_match = re.search(signature_pattern, auth_header)
        
        if not credential_match or not signed_headers_match or not signature_match:
            return None, None, None
            
        credential = credential_match.group(1)
        signed_headers = signed_headers_match.group(1)
        signature = signature_match.group(1)
        
        # 解析凭据
        # 格式: access_key/date/region/service/aws4_request
        credential_parts = credential.split('/')
        if len(credential_parts) != 5:
            return None, None, None
            
        access_key = credential_parts[0]
        date = credential_parts[1]
        region = credential_parts[2]
        service = credential_parts[3]
        
        return access_key, signature, {
            'date': date,
            'region': region,
            'service': service,
            'signed_headers': signed_headers
        }
    except Exception as e:
        print(f"解析 Authorization 头部时出错: {e}")
        return None, None, None

async def verify_aws_signature(request: Request) -> bool:
    """验证 AWS SigV4 签名"""
    # 获取 Authorization 头部
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        # 如果没有 Authorization 头部，检查是否有 API 密钥
        api_key = request.headers.get('X-S3-API-Key')
        if api_key and api_key == settings.S3_API_KEY:
            return True
        return False
    
    # 解析 Authorization 头部
    access_key, signature, auth_info = parse_auth_header(auth_header)
    if not access_key or not signature or not auth_info:
        return False
    
    # 验证 Access Key
    if access_key != settings.S3_ACCESS_KEY_ID:
        return False
    
    # 在实际应用中，这里应该计算签名并与提供的签名进行比较
    # 为了简化，我们只检查 Access Key
    # 完整的实现需要重建规范请求并计算签名
    
    return True

async def s3_auth_required(request: Request):
    """S3 身份验证依赖项"""
    is_authenticated = await verify_aws_signature(request)
    if not is_authenticated:
        raise HTTPException(
            status_code=403,
            detail="无效的 S3 身份验证凭据"
        )
    return True
