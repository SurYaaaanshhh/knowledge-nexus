import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz

from app.config import OCR_LANGUAGES, UPLOAD_DIR
from app.database import current_user_id, get_connection, user_data_directory


def _upload_directory() -> Path:
    if current_user_id() is None:
        return UPLOAD_DIR
    return user_data_directory() / "uploads"


def normalize_filename(filename: str) -> str:
    safe_name = os.path.basename(filename)
    name, ext = os.path.splitext(safe_name)
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
    return f"{safe_name or 'document'}_{uuid.uuid4().hex}{ext.lower()}"


def save_uploaded_file(file_obj, filename: str) -> dict:
    upload_directory = _upload_directory()
    upload_directory.mkdir(parents=True, exist_ok=True)
    safe_name = normalize_filename(filename)
    storage_path = upload_directory / safe_name
    with open(storage_path, "wb") as destination:
        shutil.copyfileobj(file_obj.file, destination)

    stored_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (filename, storage_name, file_path, uploaded_at, status, page_count)
            VALUES (?, ?, ?, ?, 'uploaded', 0)
            """,
            (filename, safe_name, str(storage_path), stored_at),
        )
        document_id = cursor.lastrowid

    return {
        "id": document_id,
        "filename": filename,
        "storage_name": safe_name,
        "file_path": str(storage_path),
        "status": "uploaded",
    }


def _ocr_page_text(page: fitz.Page) -> str:
    if not shutil.which("tesseract"):
        raise RuntimeError(
            "This PDF contains scanned pages. Install local OCR with `brew install tesseract`, "
            "then restart Knowledge Nexus."
        )
    try:
        text_page = page.get_textpage_ocr(language=OCR_LANGUAGES, dpi=250, full=True)
        return page.get_text("text", textpage=text_page).strip()
    except RuntimeError as error:
        raise RuntimeError(
            f"Local OCR could not read this page using language(s) {OCR_LANGUAGES!r}: {error}"
        ) from error


def extract_pdf_text_with_metadata(file_path: str) -> tuple[list[dict], int]:
    text_sections = []
    ocr_pages = 0
    with fitz.open(file_path) as doc:
        for page_number in range(doc.page_count):
            page = doc[page_number]
            page_text = page.get_text("text").strip()
            has_scanned_content = bool(page.get_images(full=True)) and (
                len(page_text) < 40 or len(re.findall(r"\w+", page_text)) < 5
            )
            if has_scanned_content:
                page_text = _ocr_page_text(page)
                if page_text:
                    ocr_pages += 1
            if page_text:
                text_sections.append({
                    "page_number": page_number + 1,
                    "text": page_text,
                })
    return text_sections, ocr_pages


def extract_pdf_text(file_path: str) -> list[dict]:
    return extract_pdf_text_with_metadata(file_path)[0]


def process_document(document_id: int, file_path: str) -> dict:
    try:
        passages, ocr_pages = extract_pdf_text_with_metadata(file_path)
    except RuntimeError:
        with get_connection() as conn:
            conn.execute("UPDATE documents SET status = 'failed' WHERE id = ?", (document_id,))
        raise
    with get_connection() as conn:
        for passage in passages:
            conn.execute(
                """
                INSERT INTO passages (document_id, page_number, text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    document_id,
                    passage["page_number"],
                    passage["text"],
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        conn.execute(
            "UPDATE documents SET status = ?, page_count = ? WHERE id = ?",
            ("processed" if passages else "failed", len(passages), document_id),
        )
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        result = dict(row)
        result["ocr_pages"] = ocr_pages
        return result


def list_documents() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_document(document_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None


def get_document_passages(document_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM passages WHERE document_id = ? ORDER BY page_number ASC",
            (document_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_document_status(document_id: int) -> dict:
    document = get_document(document_id)
    if not document:
        return {"status": "not_found"}
    return {"id": document["id"], "filename": document["filename"], "status": document["status"], "page_count": document["page_count"]}


def get_file_for_document(document_id: int) -> str | None:
    document = get_document(document_id)
    if not document:
        return None
    return document["file_path"]
