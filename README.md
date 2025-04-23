# Notion S3 API

一个将 Notion 内容转换为 S3 兼容 API 的 FastAPI 应用程序，用于与 Alist 和其他 S3 兼容客户端集成。

## 功能特点

- 自动判断 Notion ID 类型（块 ID、页面 ID、数据库 ID）
- 递归获取所有子页面及其内容
- 按页面名称组织文件夹结构
- 处理 URL 编码（如 %4d）并转换为中文字符
- 提供两种模式：
  1. API 格式返回下载链接
  2. S3 兼容格式（用于 Alist 集成）

## 安装步骤

1. 克隆仓库并进入目录：
```bash
git clone https://github.com/LynnGuo666/notion-s3-api.git
cd notion-s3-api
```

2. 创建虚拟环境并安装依赖：
```bash
python -m venv .venv
source .venv/bin/activate  # Windows 系统: .venv\Scripts\activate
pip install -r requirements.txt
```

3. 设置环境变量：
在根目录创建 `.env` 文件，内容如下：
```
NOTION_API_KEY=你的_notion_api_密钥
```

你可以通过在 https://www.notion.so/my-integrations 创建集成来获取 Notion API 密钥。

## 使用方法

1. 启动服务器：
```bash
python main.py
```

2. API 将在 http://localhost:8000 可用

## API 端点

### API 格式返回下载链接

```
GET /api/{notion_id}
```

返回 JSON 格式的数据，包含所有文件的信息和下载链接。

### S3 兼容 API

```
GET /{notion_id}
```

将 Notion ID 作为存储桶名称，返回 S3 兼容格式的数据。

```
GET /{notion_id}/{key}
```

获取指定的文件并重定向到下载链接。

### 测试 S3 兼容 API

使用 AWS CLI 测试：

```bash
# 列出存储桶中的对象
$ aws s3 ls s3://{notion_id} --endpoint-url http://localhost:8000

# 下载文件
$ aws s3 cp s3://{notion_id}/{key} ./downloaded_file --endpoint-url http://localhost:8000
```

使用 curl 测试：

```bash
# 列出存储桶中的对象
$ curl -X GET "http://localhost:8000/{notion_id}"

# 下载文件
$ curl -X GET "http://localhost:8000/{notion_id}/{key}" -L -o downloaded_file
```

## Alist 集成

要与 Alist 集成：

1. 在 Alist 中，添加新存储
2. 选择 "S3" 作为存储类型
3. 配置以下设置：
   - 存储桶名称: `你的_notion_id`
   - 端点: `http://localhost:8000`
   - 区域: 留空
   - 访问密钥 ID: 任意值（未使用）
   - 秘密访问密钥: 任意值（未使用）
   - 强制路径样式: 启用

## 许可证

MIT
