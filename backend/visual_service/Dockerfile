# 视觉编码服务。默认安装 CPU 版 torch；有 GPU 时用构建参数换成 CUDA 版：
#   --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu126
FROM python:3.11-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models

# torchvision 必须与 torch 来自同一个源，否则 nms 等运算符的 ABI 不匹配
RUN pip install --no-cache-dir --retries 10 --timeout 120 \
      --index-url "$TORCH_INDEX" torch==2.7.1 torchvision==0.22.1

RUN pip install --no-cache-dir --retries 10 --timeout 120 \
      "colpali-engine==0.3.18" "fastapi>=0.115,<1" "uvicorn>=0.30,<1" \
      "python-multipart>=0.0.9" "pillow>=10,<13"

RUN useradd --create-home --uid 10001 appuser && mkdir /models && chown appuser /models

WORKDIR /service
COPY app.py ./

USER appuser

EXPOSE 8100

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
