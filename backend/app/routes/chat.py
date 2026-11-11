from fastapi import APIRouter

from app.schemas import ChatRequest
from app.services.rag_service import answer_question

router = APIRouter(prefix="/api")


@router.post("/chat")
async def chat(request: ChatRequest):
    response = answer_question(request.question)
    return response
