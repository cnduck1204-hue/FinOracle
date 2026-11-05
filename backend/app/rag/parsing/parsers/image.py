"""图片：每一帧（多页 TIFF 的每一页）作为一页交给 OCR。"""

from pathlib import Path

from PIL import Image, ImageSequence, UnidentifiedImageError

from app.rag.parsing.errors import invalid_document, too_many_pages
from app.rag.parsing.models import ParsedDocument
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers.base import ParserProvider, image_to_png


class ImageParser(ParserProvider):
    file_types = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"})

    def parse(self, path: Path, options: ParseOptions) -> ParsedDocument:
        try:
            with Image.open(path) as image:
                frames = getattr(image, "n_frames", 1)
                if frames > options.max_pages:
                    raise too_many_pages(options.max_pages)
                document = ParsedDocument(page_count=frames)
                for page, frame in enumerate(ImageSequence.Iterator(image), start=1):
                    # 用户上传的图片不做尺寸过滤
                    png = image_to_png(frame)
                    if png is None:
                        raise invalid_document("图片无法读取")
                    document.add_image(png, reason="image_file", page=page)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
            raise invalid_document("图片损坏、过大或格式不支持") from exc
        return document
