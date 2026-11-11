from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app import database
from app.auth import (
    SESSION_COOKIE,
    authenticate_user,
    get_session_user,
    register_user,
    revoke_session,
)
from app.config import AUTH_COOKIE_SECURE
from app.routes.auth import set_session_cookie
from app.routes.documents import upload_pdf
from app.services.graph_service import get_graph, get_graph_for_entity
from app.services.pdf_service import get_document, list_documents
from app.services.rag_service import answer_question, magic_click_search, search_query
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
    revision_pdf,
    set_topic_progress,
    submit_quiz,
    update_flashcard,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
public_pages = APIRouter()
private_pages = APIRouter()


def _render(request: Request, name: str, **context):
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"request": request, **context},
    )


def _credentials(email: str, password: str) -> tuple[str, str] | None:
    try:
        from app.schemas import AuthCredentials

        model = AuthCredentials.model_validate({"email": email, "password": password})
    except ValidationError:
        return None
    return model.email, model.password


async def require_page_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    user = get_session_user(session_token)
    if user is None:
        destination = request.url.path
        if request.url.query:
            destination += f"?{request.url.query}"
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/login?next={quote(destination, safe='')}"},
        )
    with database.user_data_scope(user["id"]):
        yield user


private_pages.dependencies.append(Depends(require_page_user))


@public_pages.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/",
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    if get_session_user(session_token):
        return RedirectResponse("/", status_code=303)
    return _render(request, "auth.html", mode="login", next=next, error=None)


@public_pages.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    next: str = Form("/"),
):
    credentials = _credentials(email, password)
    if credentials is None:
        return _render(
            request, "auth.html", mode="login", next=next,
            error="Enter a valid email and a password of at least 10 characters.",
        )
    try:
        _, token = authenticate_user(*credentials)
    except HTTPException as error:
        return _render(request, "auth.html", mode="login", next=next, error=error.detail)

    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(safe_next, status_code=303)
    set_session_cookie(response, token)
    return response


@public_pages.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    if get_session_user(session_token):
        return RedirectResponse("/", status_code=303)
    return _render(request, "auth.html", mode="register", next="/", error=None)


@public_pages.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    confirmation: str = Form(),
):
    if password != confirmation:
        return _render(
            request, "auth.html", mode="register", next="/",
            error="The passwords do not match.",
        )
    credentials = _credentials(email, password)
    if credentials is None:
        return _render(
            request, "auth.html", mode="register", next="/",
            error="Enter a valid email and a password of at least 10 characters.",
        )
    try:
        _, token = register_user(*credentials)
    except HTTPException as error:
        return _render(request, "auth.html", mode="register", next="/", error=error.detail)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, token)
    return response


@public_pages.post("/logout")
async def page_logout(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
):
    revoke_session(session_token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return response


@private_pages.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(require_page_user)):
    documents = list_documents()
    return _render(
        request,
        "dashboard.html",
        user=user,
        stats=get_dashboard_stats(),
        documents=documents[:5],
        updates=get_recent_updates(4),
    )


@private_pages.get("/library", response_class=HTMLResponse)
async def library(request: Request, user=Depends(require_page_user), notice: str = ""):
    return _render(
        request, "library.html", user=user, documents=list_documents(),
        notice=notice, error=None,
    )


@private_pages.post("/library/upload", response_class=HTMLResponse)
async def library_upload(
    request: Request,
    user=Depends(require_page_user),
    files: list[UploadFile] = File(),
):
    outcomes = []
    try:
        for file in files:
            outcomes.append(await upload_pdf(file))
    except HTTPException as error:
        return _render(
            request, "library.html", user=user, documents=list_documents(),
            notice=None, error=error.detail,
        )
    notice = " ".join(
        outcome.get("message") or f"{outcome['filename']}: {outcome['facts_extracted']} verified facts extracted."
        for outcome in outcomes
    )
    return RedirectResponse(f"/library?notice={quote(notice)}", status_code=303)


@private_pages.get("/reader", response_class=HTMLResponse)
async def reader(
    request: Request,
    user=Depends(require_page_user),
    doc: int | None = None,
    page: int = 1,
    q: str = "",
):
    documents = list_documents()
    selected = get_document(doc) if doc else (get_document(documents[0]["id"]) if documents else None)
    facts = []
    updates = []
    related = magic_click_search(q, 6) if q.strip() else []
    if selected:
        from app.services.study_service import get_supported_facts

        facts = [
            fact for fact in get_supported_facts()
            if fact["document_id"] == selected["id"]
        ]
        updates = get_document_updates(selected["id"])
    return _render(
        request, "reader.html", user=user, documents=documents,
        selected=selected, facts=facts, updates=updates, page=max(page, 1),
        q=q, related=related,
    )


@private_pages.get("/reader/{document_id}/pdf")
async def reader_pdf(document_id: int):
    selected = get_document(document_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Document not found.")
    file_path = Path(selected["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found.")
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=selected["filename"],
        content_disposition_type="inline",
    )


@private_pages.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    user=Depends(require_page_user),
    q: str = "",
):
    results = search_query(q, 12) if q.strip() else []
    return _render(request, "search.html", user=user, q=q, results=results)


@private_pages.post("/search", response_class=HTMLResponse)
async def search_submit(q: str = Form()):
    return RedirectResponse(f"/search?q={quote(q.strip())}", status_code=303)


@private_pages.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    user=Depends(require_page_user),
    question: str = "",
):
    answer = answer_question(question) if question.strip() else None
    return _render(request, "chat.html", user=user, question=question, answer=answer)


@private_pages.post("/chat", response_class=HTMLResponse)
async def chat_submit(question: str = Form()):
    return RedirectResponse(f"/chat?question={quote(question.strip())}", status_code=303)


@private_pages.get("/graph", response_class=HTMLResponse)
async def graph_page(
    request: Request,
    user=Depends(require_page_user),
    entity: str = "",
):
    graph = get_graph_for_entity(entity) if entity else get_graph()
    entity_facts = []
    if entity:
        from app.services.study_service import get_supported_facts

        entity_facts = [
            fact for fact in get_supported_facts()
            if entity.casefold() in {fact["subject"].casefold(), fact["object"].casefold()}
        ]
    return _render(
        request, "graph.html", user=user, nodes=graph["nodes"],
        edges=graph["edges"], entity=entity, entity_facts=entity_facts,
    )


@private_pages.get("/quiz", response_class=HTMLResponse)
async def quiz_page(
    request: Request,
    user=Depends(require_page_user),
    topic: str = "",
    count: int = 5,
    quiz_id: int | None = None,
    score: int | None = None,
    total: int | None = None,
    message: str = "",
):
    questions = []
    if quiz_id:
        from app.database import get_connection

        with get_connection() as connection:
            row = connection.execute(
                "SELECT questions_json FROM quizzes WHERE id = ?", (quiz_id,)
            ).fetchone()
        if row:
            questions = json.loads(row["questions_json"])
    return _render(
        request, "quiz.html", user=user, topics=list_topics(), topic=topic,
        count=count if count in {5, 10, 20} else 5, quiz_id=quiz_id,
        questions=questions, score=score, total=total, message=message,
    )


@private_pages.post("/quiz/generate")
async def quiz_generate(
    topic: str = Form(""),
    count: int = Form(5),
):
    result = generate_quiz(topic or None, count if count in {5, 10, 20} else 5)
    if not result["questions"]:
        return RedirectResponse(
            f"/quiz?message={quote(result.get('message', 'No supported facts found.'))}",
            status_code=303,
        )
    return RedirectResponse(
        f"/quiz?topic={quote(topic)}&count={count}&quiz_id={result['id']}",
        status_code=303,
    )


@private_pages.post("/quiz/submit")
async def quiz_submit(
    request: Request,
    quiz_id: int = Form(),
):
    form = await request.form()
    parsed_answers = {
        key.removeprefix("answer_"): value
        for key, value in form.multi_items()
        if key.startswith("answer_") and isinstance(value, str)
    }
    result = submit_quiz(quiz_id, parsed_answers)
    if result is None:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    return RedirectResponse(
        f"/quiz?quiz_id={quiz_id}&score={result['score']}&total={result['total']}",
        status_code=303,
    )


@private_pages.get("/revision", response_class=HTMLResponse)
async def revision_page(request: Request, user=Depends(require_page_user), notice: str = ""):
    return _render(
        request, "revision.html", user=user, topics=list_topics(),
        notes=list_revision_notes(), notice=notice,
    )


@private_pages.post("/revision")
async def revision_generate(topic: str = Form(), mode: str = Form("quick")):
    if mode not in {"quick", "detailed"}:
        raise HTTPException(status_code=422, detail="Choose quick or detailed notes.")
    generate_revision_notes(topic, mode)
    return RedirectResponse(
        f"/revision?notice={quote(f'Created {mode} notes for {topic}.')}",
        status_code=303,
    )


@private_pages.get("/flashcards", response_class=HTMLResponse)
async def flashcards_page(
    request: Request,
    user=Depends(require_page_user),
    notice: str = "",
):
    return _render(
        request, "flashcards.html", user=user, topics=list_topics(),
        cards=list_flashcards(), notice=notice,
    )


@private_pages.post("/flashcards/generate")
async def flashcards_generate(topic: str = Form("")):
    cards = generate_flashcards(topic or None)
    return RedirectResponse(
        f"/flashcards?notice={quote(f'Loaded {len(cards)} flashcards.')}",
        status_code=303,
    )


@private_pages.post("/flashcards/{card_id}")
async def flashcard_update(card_id: int, status: str = Form()):
    if status not in {"remembered", "needs_revision"}:
        raise HTTPException(status_code=422, detail="Choose a valid review status.")
    result = update_flashcard(card_id, status)
    if result is None:
        raise HTTPException(status_code=404, detail="Flashcard not found.")
    return RedirectResponse("/flashcards", status_code=303)


@private_pages.get("/exam-traps", response_class=HTMLResponse)
async def exam_traps_page(
    request: Request,
    user=Depends(require_page_user),
    topic: str = "",
):
    comparisons = find_exam_traps(topic) if topic else []
    return _render(
        request, "exam_traps.html", user=user, topics=list_topics(),
        topic=topic, comparisons=comparisons,
    )


@private_pages.post("/exam-traps", response_class=HTMLResponse)
async def exam_traps_submit(topic: str = Form()):
    return RedirectResponse(f"/exam-traps?topic={quote(topic)}", status_code=303)


@private_pages.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request, user=Depends(require_page_user), notice: str = ""):
    return _render(request, "progress.html", user=user, progress=get_progress(), notice=notice)


@private_pages.post("/progress")
async def progress_update(topic: str = Form(), status: str = Form()):
    if status not in {"pending", "completed", "needs_revision"}:
        raise HTTPException(status_code=422, detail="Choose a valid topic status.")
    set_topic_progress(topic, status)
    return RedirectResponse("/progress", status_code=303)


@private_pages.get("/revision/{note_id}/pdf")
async def revision_download(note_id: int):
    content = revision_pdf(note_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Revision note not found.")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="revision-{note_id}.pdf"'},
    )
