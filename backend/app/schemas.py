from typing import Any, Dict, List, Literal, Optional
import re
from pydantic import BaseModel, Field, field_validator


class DocumentUploadResponse(BaseModel):
    id: int
    filename: str
    status: str
    pages: int


class DocumentItem(BaseModel):
    id: int
    filename: str
    status: str
    uploaded_at: str
    page_count: int
    file_url: str


class PassageItem(BaseModel):
    id: int
    document_id: int
    page_number: int
    text: str


class FactItem(BaseModel):
    id: int
    document_id: int
    subject: str
    relation: str
    object: str
    topic: str
    page_number: int
    evidence: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = 8


class SearchResult(BaseModel):
    match_type: str
    title: str
    snippet: str
    document_name: str
    page_number: int
    score: float


class MagicClickRequest(BaseModel):
    text: str = Field(..., min_length=1)
    limit: int = 10


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)


class Citation(BaseModel):
    document_name: str
    page_number: int
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    related_facts: List[Dict[str, Any]]
    citations: List[Citation]
    status: str


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    source: Optional[str] = None


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str


class QuizGenerateRequest(BaseModel):
    topic: Optional[str] = None
    count: Literal[5, 10, 20] = 5


class QuizSubmitRequest(BaseModel):
    quiz_id: int = Field(..., gt=0)
    answers: Dict[str, str]


class RevisionGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    mode: Literal["quick", "detailed"] = "quick"


class FlashcardGenerateRequest(BaseModel):
    topic: Optional[str] = None


class FlashcardUpdateRequest(BaseModel):
    status: Literal["remembered", "needs_revision"]


class TopicProgressRequest(BaseModel):
    status: Literal["pending", "completed", "needs_revision"]


class AuthCredentials(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=10, max_length=128, json_schema_extra={"writeOnly": True})

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized
