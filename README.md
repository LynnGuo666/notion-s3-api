# Notion S3 API

一个将 Notion 内容转换为 S3 兼容 API 的 FastAPI 应用程序，用于与 Alist 和其他 S3 兼容客户端集成。

## 功能特点

- 连接 Notion API 获取内容
- 提供 S3 兼容的 API 接口，支持 Alist 集成
- 处理不同类型的 Notion ID（块 ID、页面 ID、数据库 ID）
- 递归获取所有子页面及其内容
- 按页面名称在文件夹中组织内容
- 处理 URL 编码（如 %4d）并转换为中文字符
- 提供带有过期时间的直接文件链接

## 安装步骤

1. 克隆仓库：
```bash
git clone https://github.com/yourusername/notion-s3-api.git
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

3. 设置要使用的 Notion ID：
```
GET /api/notion/id?id=你的_notion_id
```

4. 刷新 Notion 数据：
```
GET /api/notion/refresh
```

5. 访问 S3 兼容 API：
```
GET /notion-s3-api
```

## API 端点

### Notion API

- `GET /api/notion/id?id={notion_id}` - 设置要使用的 Notion ID
- `GET /api/notion/refresh` - 刷新 Notion 数据
- `GET /api/files` - 列出所有文件
- `GET /api/folders` - 列出所有文件夹
- `GET /api/file/{file_id}` - 获取文件信息
- `GET /api/folder/{folder_id}` - 获取文件夹信息

### S3 兼容 API

- `GET /{bucket}` - 列出存储桶中的对象
- `GET /{bucket}/{key}` - 从存储桶获取对象

## Alist 集成

要与 Alist 集成：

1. 在 Alist 中，添加新存储
2. 选择 "S3" 作为存储类型
3. 配置以下设置：
   - 存储桶名称: `notion-s3-api`
   - 端点: `http://localhost:8000`
   - 区域: 留空
   - 访问密钥 ID: 任意值（未使用）
   - 秘密访问密钥: 任意值（未使用）
   - 强制路径样式: 启用

## 许可证

MIT
