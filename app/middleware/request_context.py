import logging
import re
import time
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def create_request_id(request: Request) -> str:
    incoming_request_id = request.headers.get("X-Request-ID")

    if (
        incoming_request_id
        and _REQUEST_ID_PATTERN.fullmatch(incoming_request_id)
    ):
        return incoming_request_id

    return uuid4().hex


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = create_request_id(request)
        request.state.request_id = request_id
        start_time = time.perf_counter()

        try:
            response = await call_next(request)

        except Exception:
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
            raise
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "elapsed_ms": round(elapsed_ms, 2),
            },
        )
        return response
