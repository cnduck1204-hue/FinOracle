"""worker 进程入口：python -m app.worker。

只加载数据库、对象存储、解析与向量化配置，不需要 JWT 等认证密钥。收到 SIGTERM / SIGINT 后
不再认领新任务，正在执行的任务完成后退出；被强制终止时，任务在租约到期后由其他 worker 接手。
"""

import asyncio
import signal

from app.core.config import WorkerSettings, get_parsing_settings
from app.core.logging import configure_logging
from app.db.engine import (
    WORKER_ISOLATION_LEVEL,
    create_engine,
    create_session_factory,
)
from app.rag.indexing import create_embedding, get_vector_store
from app.rag.pipelines import create_pipelines
from app.storage import get_storage
from app.worker.index import IndexHandler
from app.worker.parse import ParseHandler
from app.worker.pipeline import PipelineHandler
from app.worker.runtime import Worker


async def serve(settings: WorkerSettings) -> None:
    engine = create_engine(settings, isolation_level=WORKER_ISOLATION_LEVEL)
    embedding = create_embedding()
    store = get_vector_store()
    pipelines = create_pipelines(embedding, store)
    default = pipelines.default.ingest
    worker = Worker(
        create_session_factory(engine),
        get_storage(),
        [
            ParseHandler(
                get_parsing_settings(),
                auto_approve=settings.auto_approve_parsed,
                parser=default.parser,
            ),
            IndexHandler(embedding, store, indexers=default.indexers),
            PipelineHandler(pipelines),
        ],
        poll_seconds=settings.worker_poll_seconds,
        lease_seconds=settings.job_lease_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (NotImplementedError, AttributeError):
            # Windows 事件循环不支持信号处理；本机调试时按 Ctrl+C 直接退出
            pass
    try:
        await worker.run(stop)
    finally:
        await embedding.aclose()
        await engine.dispose()


def main() -> None:
    settings = WorkerSettings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
