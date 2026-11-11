import json
import re
from typing import Any

import numpy as np

from app.database import get_connection
from app.services.embedding_service import MODEL_NAME, build_embedding, build_embeddings, cosine_similarity
from app.services.study_service import get_supported_facts


MAGIC_CLICK_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are", "is",
    "on", "in", "at", "to", "of", "a", "an", "by", "its", "their", "it", "as",
    "would", "could", "should", "did", "does", "do", "has", "have", "had",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
}
MAGIC_CLICK_GENERIC_ENTITY_TERMS = {
    "bank", "card", "credit", "eligible", "effective", "every", "point", "rate",
    "reward", "spent",
}


def _magic_click_tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]{2,}\b", text.casefold()):
        if token in MAGIC_CLICK_STOP_WORDS:
            continue
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("es") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        tokens.add(token)
    return tokens


def search_query(query: str, limit: int = 8) -> list[dict]:
    with get_connection() as conn:
        passages = conn.execute(
            """
            SELECT p.*, d.filename, pe.model_name, pe.embedding_json
            FROM passages p
            JOIN documents d ON d.id = p.document_id
            LEFT JOIN passage_embeddings pe ON pe.passage_id = p.id
            ORDER BY p.id
            """
        ).fetchall()
        if not passages:
            return []
        query_embedding = build_embedding(query)
        missing = [passage for passage in passages if passage["model_name"] != MODEL_NAME]
        if missing:
            missing_embeddings = build_embeddings([passage["text"] for passage in missing])
            for passage, embedding in zip(missing, missing_embeddings, strict=True):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO passage_embeddings (passage_id, model_name, embedding_json)
                    VALUES (?, ?, ?)
                    """,
                    (passage["id"], MODEL_NAME, json.dumps(embedding.tolist())),
                )
        cached = {
            passage["id"]: passage["embedding_json"]
            for passage in passages
            if passage["model_name"] == MODEL_NAME
        }
        cached.update({
            passage["id"]: json.dumps(embedding.tolist())
            for passage, embedding in zip(missing, missing_embeddings if missing else [], strict=True)
        })

    results = []
    for passage in passages:
        passage_text = passage["text"]
        embedding = np.asarray(json.loads(cached[passage["id"]]), dtype=np.float32)
        score = cosine_similarity(query_embedding, embedding)
        if score >= 0.25 or query.lower() in passage_text.lower():
            results.append({
                "match_type": "passage",
                "title": f"Page {passage['page_number']}",
                "snippet": _relevant_snippet(passage_text, query),
                "document_name": passage["filename"],
                "page_number": passage["page_number"],
                "document_id": passage["document_id"],
                "source_url": f"/reader?doc={passage['document_id']}&page={passage['page_number']}",
                "score": round(score, 4),
            })
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def _relevant_snippet(text: str, query: str, max_chars: int = 360) -> str:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]
    query_tokens = {
        token.casefold()
        for token in re.findall(r"\b[a-zA-Z0-9][a-zA-Z0-9-]{1,}\b", query)
    }
    if not sentences:
        return text[:max_chars].strip()
    ranked = sorted(
        enumerate(sentences),
        key=lambda item: (
            len(query_tokens & {
                token.casefold()
                for token in re.findall(r"\b[a-zA-Z0-9][a-zA-Z0-9-]{1,}\b", item[1])
            }),
            -item[0],
        ),
        reverse=True,
    )
    selected = ranked[0][1]
    return selected if len(selected) <= max_chars else f"{selected[:max_chars - 1].rstrip()}…"


def magic_click_search(text: str, limit: int = 10) -> list[dict]:
    query = text.strip()
    if not query:
        return []

    normalized_query = " ".join(query.casefold().split())
    query_tokens = _magic_click_tokens(query)
    facts = get_supported_facts()
    matches = []
    for fact in facts:
        core = f"{fact['subject']} {fact['object']}"
        haystack = f"{core} {fact['relation']} {fact['topic']} {fact['evidence']}"
        normalized_haystack = " ".join(haystack.casefold().split())
        haystack_tokens = _magic_click_tokens(haystack)
        if normalized_query in normalized_haystack:
            score = 1.0
        elif query_tokens and query_tokens.issubset(haystack_tokens):
            score = 0.8
        else:
            overlap = len(query_tokens & haystack_tokens)
            core_tokens = _magic_click_tokens(core) - MAGIC_CLICK_GENERIC_ENTITY_TERMS
            core_overlap = len(query_tokens & core_tokens)
            query_coverage = overlap / len(query_tokens) if query_tokens else 0
            core_coverage = core_overlap / len(query_tokens) if query_tokens else 0
            if (
                overlap < 2
                or query_coverage < 0.35
                or (len(query_tokens) >= 3 and (core_overlap < 2 or core_coverage < 0.25))
            ):
                continue
            score = 0.7 * core_coverage + 0.3 * query_coverage
        if score:
            matches.append({
                "match_type": "fact",
                "fact_id": fact["fact_id"],
                "title": f"{fact['subject']} {fact['relation']} {fact['object']}",
                "snippet": fact["evidence"],
                "document_name": fact["filename"],
                "document_id": fact["document_id"],
                "page_number": fact["page_number"],
                "source_url": fact["source_url"],
                "score": round(score, 4),
            })
    matches.sort(key=lambda result: result["score"], reverse=True)
    return matches[:limit]


def answer_question(question: str) -> dict[str, Any]:
    stop_words = {
        "which", "what", "when", "where", "who", "whom", "whose", "why", "how",
        "did", "does", "do", "is", "are", "was", "were", "the", "a", "an",
        "in", "on", "at", "to", "from", "of", "for", "with", "and", "or",
        "my", "me", "all", "show", "tell", "about", "latest", "related",
        "information", "fact", "facts", "pdf", "uploaded", "document", "documents",
    }
    query_tokens = {
        token.casefold().rstrip("s")
        for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9-]{2,}\b", question)
        if token.casefold() not in stop_words
    }
    query_lower = question.casefold()
    relation_intent = None
    for phrase, relation in (
        ("launch", "LAUNCHED"),
        ("announc", "ANNOUNCED"),
        ("introduc", "INTRODUCED"),
        ("partner", "PARTNERED_WITH"),
    ):
        if phrase in query_lower:
            relation_intent = relation
            break
    facts = get_supported_facts()
    matched_facts = []
    for fact in facts:
        relevant_tokens = {
            token.casefold()
            for token in re.findall(
                r"\b[a-zA-Z][a-zA-Z0-9-]{2,}\b",
                f"{fact['subject']} {fact['object']} {fact['topic']}",
            )
        }
        normalized_tokens = {token.rstrip("s") for token in relevant_tokens}
        relation_matches = relation_intent is None or fact["relation"] == relation_intent
        if relation_matches and query_tokens & normalized_tokens:
            matched_facts.append(fact)

    if not matched_facts:
        return {
            "answer": "Not found in uploaded PDFs.",
            "related_facts": [],
            "citations": [],
            "status": "no_evidence",
        }

    answer_parts = []
    for fact in matched_facts[:3]:
        answer_parts.append(f"{fact['subject']} {fact['relation']} {fact['object']}.")
    answer = " ".join(answer_parts)

    citations = [
        {
            "fact_id": item["fact_id"],
            "document_id": item["document_id"],
            "document_name": item["filename"],
            "page_number": item["page_number"],
            "snippet": item["evidence"],
            "source_url": item["source_url"],
        } for item in matched_facts[:5]
    ]

    return {
        "answer": answer,
        "related_facts": [{
            "subject": fact["subject"],
            "relation": fact["relation"],
            "object": fact["object"],
            "topic": fact["topic"],
            "document_name": fact["filename"],
            "page_number": fact["page_number"],
            "source_url": fact["source_url"],
        } for fact in matched_facts[:5]],
        "citations": citations,
        "status": "success",
    }
