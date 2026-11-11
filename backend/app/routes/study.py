from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.schemas import (
    FlashcardGenerateRequest,
    FlashcardUpdateRequest,
    QuizGenerateRequest,
    QuizSubmitRequest,
    RevisionGenerateRequest,
    TopicProgressRequest,
)
from app.services.study_service import (
    find_exam_traps,
    generate_flashcards,
    generate_quiz,
    generate_revision_notes,
    get_dashboard_stats,
    get_document_updates,
    get_progress,
    get_recent_updates,
    list_flashcards,
    list_revision_notes,
    list_topics,
    quiz_history,
    revision_pdf,
    set_topic_progress,
    submit_quiz,
    update_flashcard,
)

router = APIRouter(prefix="/api")


@router.get("/stats")
async def dashboard_stats():
    return get_dashboard_stats()


@router.get("/updates")
async def updates():
    return {"updates": get_recent_updates()}


@router.get("/topics")
async def topics():
    return {"topics": list_topics()}


@router.post("/quizzes/generate")
async def quizzes_generate(request: QuizGenerateRequest):
    return generate_quiz(request.topic, request.count)


@router.post("/quizzes/submit")
async def quizzes_submit(request: QuizSubmitRequest):
    result = submit_quiz(request.quiz_id, request.answers)
    if result is None:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return result


@router.get("/quizzes/history")
async def quizzes_history():
    return quiz_history()


@router.post("/revision/generate")
async def revision_generate(request: RevisionGenerateRequest):
    return generate_revision_notes(request.topic, request.mode)


@router.get("/revision/notes")
async def revision_notes():
    return list_revision_notes()


@router.get("/revision/notes/{note_id}/pdf")
async def revision_notes_pdf(note_id: int):
    content = revision_pdf(note_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Revision note not found")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="revision-{note_id}.pdf"'},
    )


@router.post("/flashcards/generate")
async def flashcards_generate(request: FlashcardGenerateRequest):
    return {"cards": generate_flashcards(request.topic)}


@router.get("/flashcards")
async def flashcards(topic: str | None = None):
    return {"cards": list_flashcards(topic)}


@router.patch("/flashcards/{card_id}")
async def flashcards_update(card_id: int, request: FlashcardUpdateRequest):
    result = update_flashcard(card_id, request.status)
    if result is None:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    return result


@router.get("/topics/{topic}/exam-traps")
async def topic_exam_traps(topic: str):
    return {"topic": topic, "comparisons": find_exam_traps(topic)}


@router.get("/documents/{document_id}/new-connections")
async def document_new_connections(document_id: int):
    return {"document_id": document_id, "connections": get_document_updates(document_id)}


@router.get("/progress")
async def progress():
    return get_progress()


@router.patch("/progress/topics/{topic}")
async def progress_topic(topic: str, request: TopicProgressRequest):
    return set_topic_progress(topic, request.status)
