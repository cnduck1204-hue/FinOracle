from fastapi import APIRouter

from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.imports import router as imports_router
from app.api.v1.endpoints.users import router as users_router
from app.api.v1.endpoints.versions import router as versions_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])

api_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["auth"],
)

api_router.include_router(
    users_router,
    prefix="/users",
    tags=["users"],
)

api_router.include_router(admin_router, prefix="/admin", tags=["admin"])

api_router.include_router(
    documents_router, prefix="/admin/documents", tags=["documents"]
)

api_router.include_router(imports_router, prefix="/admin/imports", tags=["documents"])

api_router.include_router(versions_router, prefix="/admin/versions", tags=["documents"])
