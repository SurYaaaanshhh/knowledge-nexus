import io
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient

from app import database
from app.auth import init_auth_db
from app.database import get_connection, init_db
from app.main import app
from app.services import pdf_service
from app.services.claude_service import fallback_extract_facts
from app.services.rag_service import magic_click_search
from app.services.study_service import _fact_is_supported, get_supported_facts


client = TestClient(app)
TEST_EMAIL = "knowledge-nexus-test@example.com"
TEST_PASSWORD = "safe-test-password-123"
test_user_id = ""


@pytest.fixture(autouse=True)
def isolate_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global test_user_id
    test_user_id = ""
    client.cookies.clear()
    data_dir = tmp_path / "data"
    monkeypatch.setattr(database, "DATA_DIR", data_dir)
    monkeypatch.setattr(database, "DB_PATH", data_dir / "test.db")
    monkeypatch.setattr(pdf_service, "UPLOAD_DIR", tmp_path / "uploads")
    init_db()
    init_auth_db()
    response = client.post(
        "/api/auth/register",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert response.status_code == 201, response.text
    test_user_id = response.json()["user"]["id"]


def reset_db() -> None:
    with database.user_data_scope(test_user_id):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM document_updates")
            conn.execute("DELETE FROM flashcards")
            conn.execute("DELETE FROM revision_notes")
            conn.execute("DELETE FROM topic_progress")
            conn.execute("DELETE FROM quiz_attempts")
            conn.execute("DELETE FROM quizzes")
            conn.execute("DELETE FROM facts")
            conn.execute("DELETE FROM passages")
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM graph_nodes")
            conn.execute("DELETE FROM graph_edges")


def create_pdf(path: Path, text_blocks: list[str]) -> None:
    pdf = fitz.open()
    for idx, text in enumerate(text_blocks, start=1):
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    pdf.save(path)
    pdf.close()


def test_upload_and_extract_pdf() -> None:
    reset_db()
    pdf_path = Path("/tmp/knowledge_nexus_sample_1.pdf")
    create_pdf(pdf_path, ["SBI launched XYZ Credit Card. This is a banking product."])
    with pdf_path.open("rb") as handle:
        response = client.post("/api/upload", files={"file": (pdf_path.name, handle.read(), "application/pdf")})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["filename"] == pdf_path.name
    assert payload["status"] == "processed"


def test_scanned_pdf_pages_use_local_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    pdf = fitz.open()
    page = pdf.new_page()
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(255)
    page.insert_image(page.rect, stream=pixmap.tobytes("png"))
    pdf.save(pdf_path)
    pdf.close()

    monkeypatch.setattr(
        pdf_service,
        "_ocr_page_text",
        lambda _: "Northstar Bank launched the Orbit Rewards Credit Card.",
    )
    passages, ocr_pages = pdf_service.extract_pdf_text_with_metadata(str(pdf_path))
    assert ocr_pages == 1
    assert passages == [{
        "page_number": 1,
        "text": "Northstar Bank launched the Orbit Rewards Credit Card.",
    }]


def test_source_validation_rejects_non_entity_partnership_subject() -> None:
    unsupported = {
        "subject": "carried out",
        "relation": "PARTNERED_WITH",
        "object": "Nexora Payments",
        "evidence": "carried out in partnership with Nexora Payments",
        "source_text": "The launch was carried out in partnership with Nexora Payments.",
    }
    supported = {
        **unsupported,
        "subject": "Northstar Bank",
        "evidence": "Northstar Bank partnered with Nexora Payments",
        "source_text": "Northstar Bank partnered with Nexora Payments.",
    }
    assert not _fact_is_supported(unsupported)
    assert _fact_is_supported(supported)


def test_source_validation_rejects_article_as_graph_subject() -> None:
    unsupported = {
        "subject": "The",
        "relation": "ANNOUNCED",
        "object": "application deadline was 15 March 2026",
        "evidence": "The announced application deadline was 15 March 2026.",
        "source_text": "The announced application deadline was 15 March 2026.",
    }

    assert not _fact_is_supported(unsupported)


def test_graph_and_search_endpoints() -> None:
    reset_db()
    pdf_path = Path("/tmp/knowledge_nexus_sample_2.pdf")
    create_pdf(pdf_path, ["HDFC Bank announced ABC Credit Card.", "The card is related to RBI regulations."])
    with pdf_path.open("rb") as handle:
        response = client.post("/api/upload", files={"file": (pdf_path.name, handle.read(), "application/pdf")})
    assert response.status_code == 200, response.text

    search_response = client.post("/api/search", json={"query": "credit card", "limit": 5})
    assert search_response.status_code == 200, search_response.text
    results = search_response.json()["results"]
    assert len(results) > 0

    graph_response = client.get("/api/graph")
    assert graph_response.status_code == 200, graph_response.text
    graph_data = graph_response.json()
    assert graph_data["nodes"] or graph_data["edges"]


def test_chat_uses_citations_and_rejects_absent_facts() -> None:
    reset_db()
    pdf_path = Path("/tmp/knowledge_nexus_sample_3.pdf")
    create_pdf(pdf_path, ["RBI announced new credit card regulations."])
    with pdf_path.open("rb") as handle:
        response = client.post("/api/upload", files={"file": (pdf_path.name, handle.read(), "application/pdf")})
    assert response.status_code == 200, response.text

    answer = client.post("/api/chat", json={"question": "Which banks launched cards in the uploaded PDFs?"})
    assert answer.status_code == 200, answer.text
    response_json = answer.json()
    assert response_json["status"] == "no_evidence"
    assert "citations" in response_json

    grounded = client.post("/api/chat", json={"question": "What did RBI announce?"})
    assert grounded.status_code == 200, grounded.text
    grounded_json = grounded.json()
    assert grounded_json["status"] == "success"
    assert "RBI ANNOUNCED new credit card regulations." in grounded_json["answer"]
    assert grounded_json["citations"][0]["page_number"] == 1

    absent = client.post("/api/chat", json={"question": "Who won the Nobel Prize in 2025?"})
    assert absent.status_code == 200, absent.text
    assert absent.json()["status"] == "no_evidence"


def test_fallback_extraction_and_magic_click_return_supported_facts_only() -> None:
    reset_db()
    pdf_path = Path("/tmp/knowledge_nexus_magic_click.pdf")
    create_pdf(pdf_path, [
        "Northstar Bank launches Orbit Rewards Credit Card",
        "Northstar Bank launched the Orbit Rewards Credit Card on 12 January 2026.",
    ])
    with pdf_path.open("rb") as handle:
        response = client.post("/api/upload", files={"file": (pdf_path.name, handle.read(), "application/pdf")})
    assert response.status_code == 200, response.text

    with database.user_data_scope(test_user_id):
        facts = get_supported_facts()
    assert any(fact["subject"] == "Northstar Bank" and "Orbit Rewards Credit Card" in fact["object"] for fact in facts)
    assert all(fact["relation"] != "MENTIONS" for fact in facts)

    result = client.post("/api/magic-click", json={"text": "Orbit Rewards Credit Card"})
    assert result.status_code == 200, result.text
    results = result.json()["results"]
    assert results
    assert all(item["match_type"] == "fact" for item in results)
    assert all(item["document_name"] == pdf_path.name and item["page_number"] in {1, 2} for item in results)
    unrelated = client.post("/api/magic-click", json={"text": "spaceship on Mars"})
    assert unrelated.status_code == 200
    assert unrelated.json()["results"] == []


def test_authentication_protects_api_and_isolates_each_account() -> None:
    client.cookies.clear()
    assert client.get("/api/documents").status_code == 401

    first = client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": "first-account-password"},
    )
    assert first.status_code == 201, first.text
    assert "httponly" in first.headers["set-cookie"].lower()

    pdf_path = Path("/tmp/knowledge_nexus_private.pdf")
    create_pdf(pdf_path, ["Northstar Bank launched the Orbit Rewards Credit Card."])
    with pdf_path.open("rb") as handle:
        uploaded = client.post(
            "/api/upload",
            files={"file": (pdf_path.name, handle.read(), "application/pdf")},
        )
    assert uploaded.status_code == 200, uploaded.text
    first_document_id = uploaded.json()["id"]

    assert client.post("/api/auth/logout").status_code == 200
    second = client.post(
        "/api/auth/register",
        json={"email": "second@example.com", "password": "second-account-password"},
    )
    assert second.status_code == 201, second.text
    assert client.get("/api/documents").json() == []
    assert client.get(f"/api/documents/{first_document_id}").status_code == 404

    assert client.post("/api/auth/logout").status_code == 200
    login = client.post(
        "/api/auth/login",
        json={"email": "FIRST@example.com", "password": "first-account-password"},
    )
    assert login.status_code == 200, login.text
    assert len(client.get("/api/documents").json()) == 1


def test_authentication_rejects_duplicate_accounts_and_bad_credentials() -> None:
    duplicate = client.post(
        "/api/auth/register",
        json={"email": f" {TEST_EMAIL.upper()} ", "password": TEST_PASSWORD},
    )
    assert duplicate.status_code == 409

    bad_login = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": "not-the-right-password"},
    )
    assert bad_login.status_code == 401
    assert client.get("/api/auth/me").status_code == 200

    invalid_signup = client.post(
        "/api/auth/register",
        json={"email": "invalid", "password": "short"},
    )
    assert invalid_signup.status_code == 422


def test_authentication_temporarily_locks_after_repeated_failed_signins() -> None:
    for _ in range(5):
        response = client.post(
            "/api/auth/login",
            json={"email": TEST_EMAIL, "password": "incorrect-password-123"},
        )
        assert response.status_code == 401

    locked = client.post(
        "/api/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert locked.status_code == 429


def test_server_rendered_pages_require_auth_and_load_account_data() -> None:
    client.cookies.clear()
    protected = client.get("/", follow_redirects=False)
    assert protected.status_code == 303
    assert protected.headers["location"].startswith("/login")

    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "Sign in" in login_page.text
    assert client.get("/static/app.css").status_code == 200

    registered = client.post(
        "/register",
        data={
            "email": "page@example.com",
            "password": "page-account-password",
            "confirmation": "page-account-password",
        },
        follow_redirects=False,
    )
    assert registered.status_code == 303
    assert "httponly" in registered.headers["set-cookie"].lower()

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Study smarter with document-grounded knowledge" in dashboard.text
    for path in (
        "/library", "/reader", "/search", "/chat", "/graph", "/quiz",
        "/revision", "/flashcards", "/exam-traps", "/progress",
    ):
        page = client.get(path)
        assert page.status_code == 200, f"{path}: {page.text}"

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303


def test_magic_click_matches_long_selected_sentence_with_verb_variation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = {
        "fact_id": 1,
        "subject": "Meridian Bank",
        "relation": "LAUNCHED",
        "object": "Spark Travel Credit Card",
        "topic": "Credit Cards",
        "evidence": "Meridian Bank launches Spark Travel Credit Card",
        "filename": "March.pdf",
        "document_id": 1,
        "page_number": 1,
        "source_url": "/reader?doc=1&page=1",
    }
    unrelated_fact = {
        **fact,
        "fact_id": 2,
        "subject": "Northstar Bank",
        "object": "Orbit Rewards Credit Card",
        "evidence": "Northstar Bank launched the Orbit Rewards Credit Card in partnership with Nexora Payments",
    }
    monkeypatch.setattr("app.services.rag_service.get_supported_facts", lambda: [fact, unrelated_fact])

    results = magic_click_search(
        "Meridian Bank launched the Spark Travel Credit Card on 5 March 2026 in partnership with Nexora Payments."
    )

    assert len(results) == 1
    assert results[0]["fact_id"] == 1
    assert results[0]["score"] >= 0.35


def test_document_facts_endpoint_returns_only_supported_facts_from_selected_document() -> None:
    reset_db()
    pdf_paths = [
        Path("/tmp/knowledge_nexus_document_facts_1.pdf"),
        Path("/tmp/knowledge_nexus_document_facts_2.pdf"),
    ]
    create_pdf(pdf_paths[0], ["Northstar Bank launched the Orbit Rewards Credit Card on 12 January 2026."])
    create_pdf(pdf_paths[1], ["Meridian Bank introduced the Bloom Student Credit Card on 21 January 2026."])

    document_ids = []
    for pdf_path in pdf_paths:
        with pdf_path.open("rb") as handle:
            response = client.post(
                "/api/upload",
                files={"file": (pdf_path.name, handle.read(), "application/pdf")},
            )
        assert response.status_code == 200, response.text
        document_ids.append(response.json()["id"])

    selected_document = client.get(f"/api/documents/{document_ids[0]}/facts")
    assert selected_document.status_code == 200, selected_document.text
    facts = selected_document.json()["facts"]
    assert facts
    assert all(fact["document_id"] == document_ids[0] for fact in facts)
    assert any("Orbit Rewards Credit Card" in fact["title"] for fact in facts)
    assert all("Bloom Student Credit Card" not in fact["title"] for fact in facts)

    focused = client.get(f"/api/documents/{document_ids[0]}/facts", params={"q": "Orbit Rewards Credit Card"})
    assert focused.status_code == 200, focused.text
    assert focused.json()["facts"]
    assert all(
        "Orbit Rewards Credit Card" in f"{fact['title']} {fact['snippet']}"
        for fact in focused.json()["facts"]
    )

    missing = client.get("/api/documents/99999/facts")
    assert missing.status_code == 404


def test_fallback_parser_handles_wrapped_text_and_ignores_generic_partnership_wording() -> None:
    passage = {
        "page_number": 2,
        "text": (
            "Northstar Bank (NSB) launched the Orbit Rewards Credit Card on 12\n"
            "January 2026. The launch was carried out in partnership with Nexora Payments.\n"
            "At launch, the Orbit Rewards Credit Card offered 2\n"
            "reward points for each eligible purchase."
        ),
    }
    facts = fallback_extract_facts([passage], document_id=41)
    assert any(
        fact["subject"] == "Northstar Bank"
        and fact["relation"] == "LAUNCHED"
        and fact["object"] == "Orbit Rewards Credit Card"
        and fact["page_number"] == 2
        for fact in facts
    )
    assert any(
        fact["subject"] == "Orbit Rewards Credit Card"
        and fact["relation"] == "HAS_DETAIL"
        and fact["object"].startswith("2 reward points")
        for fact in facts
    )
    assert not any(fact["relation"] == "PARTNERED_WITH" for fact in facts)


def test_quiz_revision_flashcards_and_progress_are_source_backed() -> None:
    reset_db()
    pdf_path = Path("/tmp/knowledge_nexus_study_tools.pdf")
    create_pdf(pdf_path, [
        "SBI launched Zenith Credit Card.",
        "HDFC Bank announced Pulse Credit Card.",
    ])
    with pdf_path.open("rb") as handle:
        response = client.post("/api/upload", files={"file": (pdf_path.name, handle.read(), "application/pdf")})
    assert response.status_code == 200, response.text

    quiz_response = client.post("/api/quizzes/generate", json={"topic": "Credit Cards", "count": 5})
    assert quiz_response.status_code == 200, quiz_response.text
    quiz = quiz_response.json()
    assert quiz["questions"]
    question = quiz["questions"][0]
    assert question["citation"]["document_name"] == pdf_path.name
    assert question["citation"]["page_number"] in {1, 2}

    submitted = client.post(
        "/api/quizzes/submit",
        json={"quiz_id": quiz["id"], "answers": {"0": question["answer"]}},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["score"] == 1

    notes = client.post("/api/revision/generate", json={"topic": "Credit Cards", "mode": "detailed"})
    assert notes.status_code == 200, notes.text
    assert notes.json()["facts"]
    pdf_response = client.get(f"/api/revision/notes/{notes.json()['id']}/pdf")
    assert pdf_response.status_code == 200
    assert pdf_response.content.startswith(b"%PDF")

    cards = client.post("/api/flashcards/generate", json={"topic": "Credit Cards"})
    assert cards.status_code == 200, cards.text
    assert cards.json()["cards"]
    card = cards.json()["cards"][0]
    assert card["document_name"] == pdf_path.name
    updated = client.patch(f"/api/flashcards/{card['id']}", json={"status": "remembered"})
    assert updated.status_code == 200, updated.text

    progress = client.get("/api/progress")
    assert progress.status_code == 200, progress.text
    assert progress.json()["quiz_attempts"] == 1
    assert progress.json()["flashcards_reviewed"] == 1


def test_exam_traps_new_connections_and_source_navigation() -> None:
    reset_db()
    first_pdf = Path("/tmp/knowledge_nexus_january.pdf")
    create_pdf(first_pdf, ["SBI launched Alpha Credit Card."])
    with first_pdf.open("rb") as handle:
        first = client.post("/api/upload", files={"file": (first_pdf.name, handle.read(), "application/pdf")})
    assert first.status_code == 200, first.text

    second_pdf = Path("/tmp/knowledge_nexus_february.pdf")
    create_pdf(second_pdf, ["SBI launched Beta Credit Card."])
    with second_pdf.open("rb") as handle:
        second = client.post("/api/upload", files={"file": (second_pdf.name, handle.read(), "application/pdf")})
    assert second.status_code == 200, second.text

    comparisons = client.get("/api/topics/Credit%20Cards/exam-traps")
    assert comparisons.status_code == 200, comparisons.text
    assert comparisons.json()["comparisons"]

    connections = client.get(f"/api/documents/{second.json()['id']}/new-connections")
    assert connections.status_code == 200, connections.text
    assert connections.json()["connections"]
    assert connections.json()["connections"][0]["old"]["source_url"].startswith("/reader?doc=")
    assert connections.json()["connections"][0]["new"]["source_url"].startswith("/reader?doc=")
