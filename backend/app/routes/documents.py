import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.claude_service import extract_facts
from app.services.graph_service import sync_graph_from_facts
from app.services.pdf_service import get_document, get_document_passages, get_document_status, list_documents, process_document, save_uploaded_file
from app.services.study_service import detect_document_updates, get_supported_facts

router = APIRouter(prefix="/api")


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")
    doc = save_uploaded_file(file, file.filename)
    document_path = doc["file_path"]
    try:
        processed = process_document(doc["id"], document_path)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    passages = get_document_passages(doc["id"])
    facts = extract_facts(passages, doc["id"])
    from app.database import get_connection
    with get_connection() as conn:
        for fact in facts:
            conn.execute(
                """
                INSERT INTO facts (document_id, subject, relation, object, topic, page_number, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    fact["document_id"],
                    fact["subject"],
                    fact["relation"],
                    fact["object"],
                    fact["topic"],
                    fact["page_number"],
                    fact["evidence"],
                ),
            )
    verified_facts = get_supported_facts()
    sync_graph_from_facts(verified_facts)
    connections = detect_document_updates(doc["id"])
    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "status": processed["status"],
        "pages": processed["page_count"],
        "ocr_pages": processed["ocr_pages"],
        "facts_extracted": sum(fact["document_id"] == doc["id"] for fact in verified_facts),
        "new_connections": len(connections),
        "message": None if processed["status"] == "processed" else "No readable text was extracted. If this is a scanned PDF, install Tesseract to use local OCR.",
    }


@router.get("/documents")
async def get_documents():
    return list_documents()


@router.get("/documents/{document_id}")
async def get_document_detail(document_id: int):
    document = get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return {**document, "passages": get_document_passages(document_id)}


@router.get("/documents/{document_id}/facts")
async def get_document_facts(document_id: int, q: str | None = None):
    document = get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    query = " ".join((q or "").casefold().split())
    facts = [
        fact for fact in get_supported_facts()
        if fact["document_id"] == document_id
    ]
    if query:
        facts = [
            fact for fact in facts
            if re.search(
                rf"(?<!\w){re.escape(query)}(?!\w)",
                " ".join(
                    (fact["subject"], fact["relation"], fact["object"], fact["topic"], fact["evidence"])
                ).casefold(),
            )
        ]

    return {
        "document_id": document_id,
        "filename": document["filename"],
        "facts": [
            {
                "fact_id": fact["fact_id"],
                "title": f"{fact['subject']} {fact['relation']} {fact['object']}",
                "snippet": fact["evidence"],
                "topic": fact["topic"],
                "subject": fact["subject"],
                "relation": fact["relation"],
                "object": fact["object"],
                "document_name": fact["filename"],
                "document_id": fact["document_id"],
                "page_number": fact["page_number"],
                "source_url": fact["source_url"],
            }
            for fact in facts
        ],
    }


@router.get("/documents/{document_id}/file")
async def read_document_file(document_id: int):
    document = get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    file_path = Path(document["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(file_path, filename=document["filename"])


@router.get("/documents/{document_id}/status")
async def document_status(document_id: int):
    return get_document_status(document_id)
