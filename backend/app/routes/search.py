from fastapi import APIRouter

from app.schemas import MagicClickRequest, SearchRequest
from app.services.rag_service import magic_click_search, search_query

router = APIRouter(prefix="/api")


@router.post("/search")
async def search(request: SearchRequest):
    return {"results": search_query(request.query, request.limit)}


@router.post("/magic-click")
async def magic_click(request: MagicClickRequest):
    return {"results": magic_click_search(request.text, request.limit)}
