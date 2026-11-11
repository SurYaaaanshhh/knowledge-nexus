from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import init_auth_db, require_current_user
from app.config import BASE_DIR
from app.database import init_db
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.documents import router as documents_router
from app.routes.graph import router as graph_router
from app.routes.search import router as search_router
from app.routes.study import router as study_router
from app.services.study_service import repair_legacy_fact_records
from app.web import private_pages, public_pages


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    init_auth_db()
    repair_legacy_fact_records()
    yield


app = FastAPI(title="Knowledge Nexus", version="1.0.0", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "app" / "static")),
    name="static",
)
app.include_router(public_pages)
app.include_router(private_pages)
app.include_router(auth_router)
protected_dependencies = [Depends(require_current_user)]
app.include_router(documents_router, dependencies=protected_dependencies)
app.include_router(graph_router, dependencies=protected_dependencies)
app.include_router(search_router, dependencies=protected_dependencies)
app.include_router(chat_router, dependencies=protected_dependencies)
app.include_router(study_router, dependencies=protected_dependencies)


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok"}
