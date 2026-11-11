"""解析参数与默认值：输入限制、PDF 与 OCR 阈值、切片长度。

这些值属于解析算法，随代码一起修改和测试，不从环境变量读取。与部署机器相关的配置
（Gotenberg 地址、LibreOffice 路径）定义在 app.core.config.ParsingSettings，
由服务层据此创建格式转换器。
"""

from dataclasses import dataclass

MIB = 1024 * 1024
# 按名称选择 OCR 或切片器时，由系统按文档和环境自动选择
AUTO = "auto"
DEFAULT_MIN_OCR_CONFIDENCE = 0.5
DEFAULT_MAX_CHUNK_CHARS = 800
DEFAULT_CHUNK_OVERLAP = 100


@dataclass(frozen=True, slots=True)
class ParseOptions:
    # 输入限制
    max_file_bytes: int = 50 * MIB
    max_pages: int = 500
    # PDF：文本层字数不少于该值才视为有效文本层；不足且含图片的页整页 OCR，
    # 已被有效文本层覆盖的图片区域不再 OCR
    min_text_layer_chars: int = 20
    # PDF：面积占页面比例不低于该值的图片单独 OCR
    min_image_area_ratio: float = 0.1
    # 小于该像素数的图片视为图标或装饰，不识别
    min_image_pixels: int = 40_000
    render_dpi: int = 200
    max_render_side: int = 4000
    min_ocr_confidence: float = DEFAULT_MIN_OCR_CONFIDENCE
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
