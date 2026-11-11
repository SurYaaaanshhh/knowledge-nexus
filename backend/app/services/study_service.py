import json
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import quote

import fitz

from app.database import get_connection


SUPPORTED_RELATIONS = {
    "LAUNCHED",
    "ANNOUNCED",
    "PARTNERED_WITH",
    "INTRODUCED",
    "HAS_DETAIL",
    "RELATED_TO",
}


def _source_url(document_id: int, page_number: int, query: str = "") -> str:
    url = f"/reader?doc={document_id}&page={page_number}"
    return f"{url}&q={quote(query, safe='')}" if query else url


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _fact_is_supported(fact: Any) -> bool:
    relation = str(fact["relation"]).upper()
    if relation not in SUPPORTED_RELATIONS:
        return False
    raw_subject = str(fact["subject"]).strip()
    if not raw_subject or raw_subject.casefold() in {"the", "a", "an"} or not raw_subject[0].isupper():
        return False
    subject = _normalise_text(str(fact["subject"]))
    object_name = _normalise_text(str(fact["object"]))
    evidence = _normalise_text(str(fact["evidence"]))
    passage = _normalise_text(str(fact["source_text"]))
    if not subject or not object_name or not evidence or evidence not in passage:
        return False
    if subject not in evidence or object_name not in evidence:
        return False
    subject_position = evidence.find(subject)
    object_position = evidence.find(object_name, subject_position + len(subject))
    if object_position < 0:
        return False
    relation_context = evidence[subject_position + len(subject):object_position]
    relation_terms = {
        "LAUNCHED": ("launch", "introduced"),
        "ANNOUNCED": ("announc",),
        "PARTNERED_WITH": ("partner",),
        "INTRODUCED": ("introduc",),
        "HAS_DETAIL": ("offered", "includes", "required", "available", "amount was"),
        "RELATED_TO": ("related to", "associated with"),
    }
    return any(term in relation_context for term in relation_terms[relation])


def get_supported_facts(topic: str | None = None) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT f.id AS fact_id, f.document_id, d.filename, f.subject, f.relation,
                   f.object, f.topic, f.page_number, f.evidence, p.text AS source_text
            FROM facts f
            JOIN documents d ON d.id = f.document_id
            JOIN passages p ON p.document_id = f.document_id AND p.page_number = f.page_number
            ORDER BY f.id
            """
        ).fetchall()
    result = []
    for row in rows:
        if topic and topic.casefold() not in str(row["topic"]).casefold():
            continue
        if _fact_is_supported(row):
            fact = dict(row)
            fact["source_url"] = _source_url(fact["document_id"], fact["page_number"], fact["object"])
            result.append(fact)
    return result


def repair_legacy_fact_records() -> None:
    """Replace legacy generic mention rows with source-backed local extraction."""
    from app.services.claude_service import fallback_extract_facts
    from app.services.graph_service import sync_graph_from_facts

    with get_connection() as conn:
        affected = conn.execute(
            """
            SELECT DISTINCT d.id, d.filename
            FROM documents d
            JOIN facts f ON f.document_id = d.id
            WHERE f.relation = 'MENTIONS' AND f.object = 'Topic Context'
            """
        ).fetchall()
    for document in affected:
        with get_connection() as conn:
            passages = [
                dict(row) for row in conn.execute(
                    "SELECT page_number, text FROM passages WHERE document_id = ? ORDER BY page_number",
                    (document["id"],),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM facts WHERE document_id = ? AND relation = 'MENTIONS' AND object = 'Topic Context'",
                (document["id"],),
            )
        facts = fallback_extract_facts(passages, document["id"])
        with get_connection() as conn:
            for fact in facts:
                exists = conn.execute(
                    """
                    SELECT 1 FROM facts
                    WHERE document_id = ? AND lower(subject) = lower(?)
                      AND relation = ? AND lower(object) = lower(?)
                      AND page_number = ?
                    LIMIT 1
                    """,
                    (
                        fact["document_id"], fact["subject"], fact["relation"],
                        fact["object"], fact["page_number"],
                    ),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO facts
                        (document_id, subject, relation, object, topic, page_number, evidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact["document_id"], fact["subject"], fact["relation"], fact["object"],
                        fact["topic"], fact["page_number"], fact["evidence"],
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )

    sync_graph_from_facts(get_supported_facts())


def list_topics() -> list[str]:
    return sorted({fact["topic"] for fact in get_supported_facts() if fact["topic"] and fact["topic"] != "General"})


def generate_quiz(topic: str | None, requested_count: int) -> dict:
    facts = get_supported_facts(topic)
    if not facts:
        return {"topic": topic or "Mixed topics", "questions": [], "message": "No verified facts found for this topic."}

    questions = []
    for fact in facts:
        relation_word = fact["relation"].replace("_", " ").lower()
        if fact["relation"] == "PARTNERED_WITH":
            prompt = f"Which organization partnered with {fact['subject']}?"
        elif fact["relation"] == "HAS_DETAIL":
            prompt = f"What detail did the source report about {fact['subject']}?"
        else:
            prompt = f"What did {fact['subject']} {relation_word}?"
        choices = sorted({
            other["object"]
            for other in facts
            if other["relation"] == fact["relation"]
            and other["topic"].casefold() == fact["topic"].casefold()
            and other["object"].casefold() != fact["object"].casefold()
        })
        question_type = "multiple_choice" if len(choices) >= 3 else "short_answer"
        if question_type == "multiple_choice":
            options = [fact["object"], *choices[:3]]
            options = sorted(options, key=str.casefold)
        else:
            options = []
        questions.append({
            "question": prompt,
            "type": question_type,
            "options": options,
            "answer": fact["object"],
            "explanation": fact["evidence"],
            "fact_id": fact["fact_id"],
            "citation": {
                "document_id": fact["document_id"],
                "document_name": fact["filename"],
                "page_number": fact["page_number"],
                "snippet": fact["evidence"],
                "source_url": fact["source_url"],
            },
        })
    questions = questions[:requested_count]
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO quizzes (topic, questions_json, created_at) VALUES (?, ?, ?)",
            (
                topic or "Mixed topics",
                json.dumps(questions),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        quiz_id = cursor.lastrowid
    return {"id": quiz_id, "topic": topic or "Mixed topics", "questions": questions}


def submit_quiz(quiz_id: int, answers: dict[str, str]) -> dict | None:
    with get_connection() as conn:
        quiz = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
        if not quiz:
            return None
        questions = json.loads(quiz["questions_json"])
        reviewed = []
        score = 0
        for index, question in enumerate(questions):
            supplied = str(answers.get(str(index), "")).strip()
            correct = supplied.casefold() == str(question["answer"]).casefold()
            score += int(correct)
            reviewed.append({
                **question,
                "selected_answer": supplied,
                "correct": correct,
            })
        cursor = conn.execute(
            """
            INSERT INTO quiz_attempts (quiz_id, answers_json, score, total, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                quiz_id,
                json.dumps(answers),
                score,
                len(questions),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.execute(
            """
            INSERT INTO topic_progress (topic, status, last_studied)
            VALUES (?, 'in_progress', ?)
            ON CONFLICT(topic) DO UPDATE SET last_studied = excluded.last_studied
            """,
            (
                quiz["topic"],
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        return {
            "attempt_id": cursor.lastrowid,
            "score": score,
            "total": len(questions),
            "questions": reviewed,
        }


def quiz_history() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.quiz_id, q.topic, a.score, a.total, a.created_at
            FROM quiz_attempts a JOIN quizzes q ON q.id = a.quiz_id
            ORDER BY a.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def generate_revision_notes(topic: str, mode: str) -> dict:
    facts = get_supported_facts(topic)
    if not facts:
        return {"topic": topic, "mode": mode, "facts": [], "message": "No verified facts found for this topic."}
    notes = []
    seen = set()
    for fact in facts:
        key = (fact["subject"].casefold(), fact["relation"], fact["object"].casefold(), fact["document_id"])
        if key in seen:
            continue
        seen.add(key)
        notes.append({
            "text": fact["evidence"] if mode == "detailed" else f"{fact['subject']} {fact['relation'].replace('_', ' ').lower()} {fact['object']}.",
            "fact_id": fact["fact_id"],
            "document_id": fact["document_id"],
            "document_name": fact["filename"],
            "page_number": fact["page_number"],
            "snippet": fact["evidence"],
            "source_url": fact["source_url"],
        })
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO revision_notes (topic, mode, content, created_at) VALUES (?, ?, ?, ?)",
            (
                topic,
                mode,
                json.dumps(notes),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.execute(
            """
            INSERT INTO topic_progress (topic, status, last_studied)
            VALUES (?, 'in_progress', ?)
            ON CONFLICT(topic) DO UPDATE SET last_studied = excluded.last_studied
            """,
            (topic, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        note_id = cursor.lastrowid
    return {"id": note_id, "topic": topic, "mode": mode, "facts": notes}


def list_revision_notes() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM revision_notes ORDER BY id DESC").fetchall()
    return [
        {**dict(row), "facts": json.loads(row["content"])}
        for row in rows
    ]


def revision_pdf(note_id: int) -> bytes | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM revision_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    facts = json.loads(row["content"])
    pdf = fitz.open()
    page = pdf.new_page()
    y = 55
    page.insert_text((50, y), f"{row['topic']} — {row['mode'].title()} Revision", fontsize=18)
    y += 35
    for fact in facts:
        for line in (fact["text"], f"Source: {fact['document_name']}, page {fact['page_number']}"):
            if y > 770:
                page = pdf.new_page()
                y = 55
            page.insert_textbox((50, y, 545, y + 38), line, fontsize=10)
            y += 45
    output = BytesIO()
    pdf.save(output)
    pdf.close()
    return output.getvalue()


def generate_flashcards(topic: str | None = None) -> list[dict]:
    facts = get_supported_facts(topic)
    with get_connection() as conn:
        for fact in facts:
            question = f"What did {fact['subject']} {fact['relation'].replace('_', ' ').lower()}?"
            conn.execute(
                """
                INSERT OR IGNORE INTO flashcards
                    (fact_id, question, answer, review_status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (
                    fact["fact_id"], question, fact["object"],
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    return list_flashcards(topic)


def list_flashcards(topic: str | None = None) -> list[dict]:
    facts_by_id = {fact["fact_id"]: fact for fact in get_supported_facts(topic)}
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.*, f.document_id, f.page_number, f.evidence, f.topic, d.filename
            FROM flashcards c
            JOIN facts f ON f.id = c.fact_id
            JOIN documents d ON d.id = f.document_id
            ORDER BY c.id
            """
        ).fetchall()
    cards = []
    for row in rows:
        fact = facts_by_id.get(row["fact_id"])
        if not fact:
            continue
        cards.append({
            "id": row["id"],
            "fact_id": row["fact_id"],
            "question": row["question"],
            "answer": row["answer"],
            "review_status": row["review_status"],
            "topic": row["topic"],
            "document_id": row["document_id"],
            "document_name": row["filename"],
            "page_number": row["page_number"],
            "snippet": row["evidence"],
            "source_url": _source_url(row["document_id"], row["page_number"], row["answer"]),
        })
    return cards


def update_flashcard(card_id: int, status: str) -> dict | None:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE flashcards SET review_status = ? WHERE id = ?",
            (status, card_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            """
            SELECT c.*, f.topic FROM flashcards c
            JOIN facts f ON f.id = c.fact_id
            WHERE c.id = ?
            """,
            (card_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO topic_progress (topic, status, last_studied)
            VALUES (?, 'in_progress', ?)
            ON CONFLICT(topic) DO UPDATE SET last_studied = excluded.last_studied
            """,
            (
                row["topic"],
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
    return dict(row)


def find_exam_traps(topic: str) -> list[dict]:
    facts = [fact for fact in get_supported_facts(topic) if fact["topic"].casefold() == topic.casefold()]
    comparisons = []
    seen = set()
    for index, left in enumerate(facts):
        for right in facts[index + 1:]:
            if left["document_id"] == right["document_id"]:
                continue
            if (
                left["subject"].casefold() == right["subject"].casefold()
                and left["object"].casefold() == right["object"].casefold()
            ):
                continue
            key = tuple(sorted((left["fact_id"], right["fact_id"])))
            if key in seen:
                continue
            seen.add(key)
            comparisons.append({
                "field": "Source-backed facts",
                "fact_a": {
                    "fact_id": left["fact_id"], "subject": left["subject"],
                    "relation": left["relation"], "object": left["object"],
                    "document_id": left["document_id"], "document_name": left["filename"],
                    "page_number": left["page_number"], "snippet": left["evidence"],
                    "source_url": left["source_url"],
                },
                "fact_b": {
                    "fact_id": right["fact_id"], "subject": right["subject"],
                    "relation": right["relation"], "object": right["object"],
                    "document_id": right["document_id"], "document_name": right["filename"],
                    "page_number": right["page_number"], "snippet": right["evidence"],
                    "source_url": right["source_url"],
                },
                "why_compare": f"These facts share the topic “{topic}” but differ in their source-stated details.",
            })
    return comparisons


def detect_document_updates(document_id: int) -> list[dict]:
    facts = [fact for fact in get_supported_facts() if fact["document_id"] == document_id]
    updates = []
    with get_connection() as conn:
        for new_fact in facts:
            old_candidates = conn.execute(
                """
                SELECT f.id AS fact_id, f.document_id, f.subject, f.relation, f.object,
                       f.topic, f.page_number, f.evidence, d.filename
                FROM facts f JOIN documents d ON d.id = f.document_id
                WHERE f.document_id != ? AND lower(f.topic) = lower(?)
                ORDER BY f.id
                """,
                (document_id, new_fact["topic"]),
            ).fetchall()
            for old_row in old_candidates:
                old = dict(old_row)
                if (
                    old["subject"].casefold() == new_fact["subject"].casefold()
                    and old["object"].casefold() == new_fact["object"].casefold()
                    and old["relation"] == new_fact["relation"]
                ):
                    continue
                # Only compare rows when the source explicitly shares an entity/product name.
                shared_terms = {
                    token.casefold()
                    for token in re.findall(r"[A-Za-z0-9]{3,}", f"{old['subject']} {old['object']}")
                } & {
                    token.casefold()
                    for token in re.findall(r"[A-Za-z0-9]{3,}", f"{new_fact['subject']} {new_fact['object']}")
                }
                if not shared_terms:
                    continue
                description = "New related information; the sources share a named entity or product."
                conn.execute(
                    """
                    INSERT OR IGNORE INTO document_updates
                        (new_fact_id, existing_fact_id, description, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        new_fact["fact_id"], old["fact_id"], description,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
                updates.append({
                    "description": description,
                    "old": {
                        "fact_id": old["fact_id"], "subject": old["subject"],
                        "relation": old["relation"], "object": old["object"],
                        "document_id": old["document_id"], "document_name": old["filename"],
                        "page_number": old["page_number"], "snippet": old["evidence"],
                        "source_url": _source_url(old["document_id"], old["page_number"], old["object"]),
                    },
                    "new": {
                        "fact_id": new_fact["fact_id"], "subject": new_fact["subject"],
                        "relation": new_fact["relation"], "object": new_fact["object"],
                        "document_id": new_fact["document_id"], "document_name": new_fact["filename"],
                        "page_number": new_fact["page_number"], "snippet": new_fact["evidence"],
                        "source_url": new_fact["source_url"],
                    },
                })
    return updates


def get_document_updates(document_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.description,
                   old.id AS old_fact_id, old.subject AS old_subject, old.relation AS old_relation,
                   old.object AS old_object, old.page_number AS old_page,
                   old.evidence AS old_evidence, old.document_id AS old_document_id,
                   old_doc.filename AS old_filename,
                   new.id AS new_fact_id, new.subject AS new_subject, new.relation AS new_relation,
                   new.object AS new_object, new.page_number AS new_page,
                   new.evidence AS new_evidence, new.document_id AS new_document_id,
                   new_doc.filename AS new_filename
            FROM document_updates u
            JOIN facts old ON old.id = u.existing_fact_id
            JOIN facts new ON new.id = u.new_fact_id
            JOIN documents old_doc ON old_doc.id = old.document_id
            JOIN documents new_doc ON new_doc.id = new.document_id
            WHERE new.document_id = ?
            ORDER BY u.id
            """,
            (document_id,),
        ).fetchall()
    result = []
    for row in rows:
        result.append({
            "description": row["description"],
            "old": {
                "fact_id": row["old_fact_id"], "subject": row["old_subject"],
                "relation": row["old_relation"], "object": row["old_object"],
                "document_id": row["old_document_id"], "document_name": row["old_filename"],
                "page_number": row["old_page"], "snippet": row["old_evidence"],
                "source_url": _source_url(row["old_document_id"], row["old_page"], row["old_object"]),
            },
            "new": {
                "fact_id": row["new_fact_id"], "subject": row["new_subject"],
                "relation": row["new_relation"], "object": row["new_object"],
                "document_id": row["new_document_id"], "document_name": row["new_filename"],
                "page_number": row["new_page"], "snippet": row["new_evidence"],
                "source_url": _source_url(row["new_document_id"], row["new_page"], row["new_object"]),
            },
        })
    return result


def get_progress() -> dict:
    topics = list_topics()
    with get_connection() as conn:
        progress_rows = conn.execute("SELECT * FROM topic_progress").fetchall()
        attempt = conn.execute(
            "SELECT COALESCE(SUM(score), 0) AS score, COALESCE(SUM(total), 0) AS total, COUNT(*) AS count FROM quiz_attempts"
        ).fetchone()
        reviewed = conn.execute(
            "SELECT COUNT(*) FROM flashcards WHERE review_status != 'pending'"
        ).fetchone()[0]
    progress_by_topic = {row["topic"]: dict(row) for row in progress_rows}
    recent_topics = sorted(
        (
            progress_by_topic[topic]
            for topic in topics
            if progress_by_topic.get(topic, {}).get("last_studied")
        ),
        key=lambda item: item["last_studied"],
        reverse=True,
    )
    return {
        "total_topics": len(topics),
        "completed_topics": sum(progress_by_topic.get(topic, {}).get("status") == "completed" for topic in topics),
        "needs_revision": sum(progress_by_topic.get(topic, {}).get("status") == "needs_revision" for topic in topics),
        "pending_topics": sum(progress_by_topic.get(topic, {}).get("status", "pending") == "pending" for topic in topics),
        "quiz_attempts": attempt["count"],
        "quiz_accuracy": round(attempt["score"] * 100 / attempt["total"]) if attempt["total"] else 0,
        "flashcards_reviewed": reviewed,
        "recently_studied": recent_topics[:5],
        "topics": [
            {
                "topic": topic,
                "status": progress_by_topic.get(topic, {}).get("status", "pending"),
                "last_studied": progress_by_topic.get(topic, {}).get("last_studied"),
            }
            for topic in topics
        ],
    }


def get_dashboard_stats() -> dict:
    facts = get_supported_facts()
    with get_connection() as conn:
        documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        graph_links = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    return {
        "documents": documents,
        "facts": len(facts),
        "topics": len({fact["topic"] for fact in facts if fact["topic"] != "General"}),
        "graph_links": graph_links,
    }


def get_recent_updates(limit: int = 8) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.description,
                   old.subject AS old_subject, old.relation AS old_relation,
                   old.object AS old_object, old.page_number AS old_page,
                   old.evidence AS old_evidence, old.document_id AS old_document_id,
                   old_doc.filename AS old_filename,
                   new.subject AS new_subject, new.relation AS new_relation,
                   new.object AS new_object, new.page_number AS new_page,
                   new.evidence AS new_evidence, new.document_id AS new_document_id,
                   new_doc.filename AS new_filename
            FROM document_updates u
            JOIN facts old ON old.id = u.existing_fact_id
            JOIN facts new ON new.id = u.new_fact_id
            JOIN documents old_doc ON old_doc.id = old.document_id
            JOIN documents new_doc ON new_doc.id = new.document_id
            ORDER BY u.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        result.append({
            "description": row["description"],
            "old": {
                "subject": row["old_subject"], "relation": row["old_relation"],
                "object": row["old_object"], "document_id": row["old_document_id"],
                "document_name": row["old_filename"], "page_number": row["old_page"],
                "snippet": row["old_evidence"],
                "source_url": _source_url(row["old_document_id"], row["old_page"], row["old_object"]),
            },
            "new": {
                "subject": row["new_subject"], "relation": row["new_relation"],
                "object": row["new_object"], "document_id": row["new_document_id"],
                "document_name": row["new_filename"], "page_number": row["new_page"],
                "snippet": row["new_evidence"],
                "source_url": _source_url(row["new_document_id"], row["new_page"], row["new_object"]),
            },
        })
    return result


def set_topic_progress(topic: str, status: str) -> dict:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO topic_progress (topic, status, last_studied)
            VALUES (?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
                status = excluded.status,
                last_studied = excluded.last_studied
            """,
            (topic, status, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    return {"topic": topic, "status": status}
