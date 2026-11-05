# 解析接口测试文件

用于在 Apifox 中手动测试 `POST /api/v1/admin/documents/parse`。内容均为虚构的“测试用”制度，不代表真实规定。

## 启动后端

以下命令在 `backend/` 执行。先生成本地配置，再在 `.env` 中填写管理员账号：用户名 3–32 位字母、数字或下划线，密码 8–128 位且同时包含字母和数字。

```sh
python scripts/init_env.py
```

**方式一：全部用 Docker**。扫描件由镜像内置的 Tesseract 识别，全部文件都能测试。

```sh
docker compose up --build -d --wait
```

API 地址为 `http://127.0.0.1:8001`。

**方式二：本机运行 API（RapidOCR，识别更准）**。在 `.env` 中另外设置 `GOTENBERG_URL=http://127.0.0.1:3000`，然后：

```sh
docker compose up -d --wait mysql redis gotenberg
uv sync --locked --extra ocr
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API 地址为 `http://127.0.0.1:8000`。

## Apifox 步骤

1. 导入 → OpenAPI/Swagger → URL：`<API 地址>/openapi.json`，环境的前置 URL 设为 `<API 地址>`。
2. 调用 `POST /api/v1/auth/login`，JSON 为 `{"identifier": "管理员用户名", "password": "管理员密码"}`，复制返回的 `access_token`。
3. 打开 `POST /api/v1/admin/documents/parse`，Auth 选 Bearer Token 填入令牌；Body 选 form-data，`file` 选择下表文件。`ocr`、`chunker` 等字段可不填。
4. 对照下表检查 `status`、`chunker`、`converter`、`chunks`；需要查看清洗后的文本块时将 `include_blocks` 设为 `true`。

## 预期结果

| 文件 | 测试点 | 预期 |
| --- | --- | --- |
| `01_制度条文_文字版.pdf` | 三页文字 PDF：章 / 条、表格、页眉页脚、行首的条文引用 | `completed`，`article`，8 个切片（第一条至第八条），页码 1–3；`stats.removed_lines` 为 6（页眉、页码）；第四条包含“第二条规定的豁免情形除外。”；第六条包含表格 |
| `02_制度条文_扫描版.pdf` | 无文字层的扫描页 | `completed`，`article`，4 个切片（标题 + 第一条至第三条）。RapidOCR 逐字正确；Tesseract 括号识别为 `〈`、`)`，“客户”识别为“客己”。`ocr=none` 时为 `needs_ocr`，警告“第 1 页待 OCR” |
| `03_通知.docx` | Word 标题、“一、二、三、”序号、表格 | `completed`，`item`，4 个切片（前言 + 一、二、三、）；表格在“二、”中；无页码 |
| `04_旧版规定.doc` | Word 97–2003，先转 PDF | `completed`，`article`，`converter` 为 `gotenberg`（未配置时为 `libreoffice`），5 个切片（标题 + 第一条至第四条），页码 1 |
| `05_操作指南.md` | Markdown 标题层级 | `completed`，`length`，4 个切片，`heading` 为“数据报送操作指南（测试用） / 报送流程 / 准备数据”等路径 |
| `06_理财办法_GB18030.txt` | GB18030 编码 | `completed`，`article`，4 个切片（标题 + 第一条至第三条），中文无乱码 |
| `07_通知_扫描图片.png` | 图片 OCR | `completed`，`item`，3 个切片（标题 + 一、二、）。Tesseract 括号识别为 `〈`、`)`；`ocr=none` 时为 `needs_ocr` |
| `errors/不支持的表格.xlsx` | 不支持的类型 | 415 `DOCUMENT_UNSUPPORTED_TYPE` |
| `errors/伪装的文档.doc` | 纯文本改扩展名 | 422 `DOCUMENT_INVALID`：“文件内容不是有效的 .doc 文档” |
| `errors/空文件.txt` | 空文件 | 422 `DOCUMENT_INVALID`：“文件为空” |

其他可测错误：不带令牌返回 401，普通用户令牌返回 403 `ADMIN_REQUIRED`，`max_chunk_chars=200` 且 `chunk_overlap=300` 返回 422 `REQUEST_VALIDATION_ERROR`，方式二中停止 Gotenberg 后上传 `.doc` 返回 503 `DOCUMENT_CONVERSION_UNAVAILABLE`。
