from typing import Annotated

from fastapi import APIRouter, Depends, Form, Response

from app.api.dependencies import require_admin
from app.core.config import ParsingSettings, get_parsing_settings
from app.rag.parsing import ParseResult
from app.schemas.document import ParseUploadForm
from app.services import document_parsing

router = APIRouter(dependencies=[Depends(require_admin)])


@router.post(
    "/parse",
    response_model=ParseResult,
    summary="上传文档并返回解析结果（预览，不入库）",
)
async def parse_document(
    # 显式声明 multipart，接口文档导入 Apifox 等工具后才能直接选择文件
    form: Annotated[ParseUploadForm, Form(media_type="multipart/form-data")],
    response: Response,
    settings: Annotated[ParsingSettings, Depends(get_parsing_settings)],
) -> ParseResult:
    response.headers["Cache-Control"] = "no-store"
    return await document_parsing.parse_upload(form.file, form, settings)
