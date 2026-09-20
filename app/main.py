from fastapi import FastAPI

from app.api.v1.router import api_router


app = FastAPI(
    title="FinOracle API",
    description="基于 RAG 的金融制度类知识问答系统",
    version="0.1.0",
)

app.include_router(
    api_router,
    prefix="/api/v1",
)