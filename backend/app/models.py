from dataclasses import dataclass
from typing import Optional


@dataclass
class DocumentRecord:
    id: Optional[int]
    filename: str
    storage_name: str
    file_path: str
    uploaded_at: str
    status: str = "uploaded"
    page_count: int = 0


@dataclass
class PassageRecord:
    id: Optional[int]
    document_id: int
    page_number: int
    text: str
    created_at: str


@dataclass
class FactRecord:
    id: Optional[int]
    document_id: int
    subject: str
    relation: str
    obj: str
    topic: str
    page_number: int
    evidence: str
    created_at: str
