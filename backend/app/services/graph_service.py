from typing import Any
from urllib.parse import quote

from app.database import get_connection


def _normalise_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def sync_graph_from_facts(facts: list[dict]) -> list[dict]:
    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []

    for fact in facts:
        subject = str(fact.get("subject", "Unknown")).strip()
        obj = str(fact.get("object", "Unknown")).strip()
        topic = str(fact.get("topic", "General")).strip()
        relation = fact.get("relation", "RELATED_TO").strip().upper()
        if not subject or not obj or not topic:
            continue

        subject_key = _normalise_name(subject)
        object_key = _normalise_name(obj)
        topic_key = _normalise_name(topic)
        nodes.setdefault(subject_key, {"id": subject_key, "label": subject, "type": "entity"})
        nodes.setdefault(object_key, {"id": object_key, "label": obj, "type": "entity"})
        nodes.setdefault(topic_key, {"id": topic_key, "label": topic, "type": "topic"})
        edges.append({"source": subject_key, "target": object_key, "label": relation})
        edges.append({"source": object_key, "target": topic_key, "label": "HAS_TOPIC"})

    with get_connection() as conn:
        conn.execute("DELETE FROM graph_nodes")
        conn.execute("DELETE FROM graph_edges")
        for node in nodes.values():
            conn.execute(
                "INSERT OR REPLACE INTO graph_nodes (id, label, node_type, source) VALUES (?, ?, ?, ?)",
                (node["id"], node["label"], node["type"], "fact_extraction"),
            )
        for edge in edges:
            conn.execute(
                "INSERT INTO graph_edges (source, target, label) VALUES (?, ?, ?)",
                (edge["source"], edge["target"], edge["label"]),
            )

    return [
        {"id": node["id"], "label": node["label"], "type": node["type"]} for node in nodes.values()
    ]


def get_graph() -> dict[str, Any]:
    with get_connection() as conn:
        nodes = [dict(row) for row in conn.execute("SELECT * FROM graph_nodes ORDER BY label").fetchall()]
        edges = [dict(row) for row in conn.execute("SELECT * FROM graph_edges ORDER BY id").fetchall()]
    return {"nodes": nodes, "edges": edges}


def get_graph_for_entity(entity_name: str) -> dict[str, Any]:
    normalized = _normalise_name(entity_name)
    with get_connection() as conn:
        node = conn.execute("SELECT * FROM graph_nodes WHERE id = ? OR label = ? LIMIT 1", (normalized, entity_name)).fetchone()
        if not node:
            return {"nodes": [], "edges": []}
        related_edges = conn.execute(
            "SELECT * FROM graph_edges WHERE source = ? OR target = ?",
            (node["id"], node["id"]),
        ).fetchall()
        related_ids = set()
        for row in related_edges:
            related_ids.add(row["source"])
            related_ids.add(row["target"])
        node_rows = conn.execute(
            "SELECT * FROM graph_nodes WHERE id IN ({})".format(", ".join("?" for _ in related_ids)),
            tuple(sorted(related_ids)),
        ).fetchall() if related_ids else []
    return {"nodes": [dict(row) for row in node_rows], "edges": [dict(row) for row in related_edges]}


def get_topic_timeline(topic_name: str) -> list[dict]:
    from app.services.study_service import get_supported_facts

    related = [
        fact for fact in get_supported_facts()
        if topic_name.casefold() in " ".join(
            (fact["topic"], fact["subject"], fact["object"])
        ).casefold()
    ]
    related.sort(key=lambda fact: (fact["filename"].casefold(), fact["page_number"]))
    return [
        {
            "fact_id": fact["fact_id"],
            "document_id": fact["document_id"],
            "filename": fact["filename"],
            "page_number": fact["page_number"],
            "subject": fact["subject"],
            "relation": fact["relation"],
            "object": fact["object"],
            "topic": fact["topic"],
            "evidence": fact["evidence"],
            "source_url": (
                f"/reader?doc={fact['document_id']}&page={fact['page_number']}"
                f"&q={quote(fact['object'], safe='')}"
            ),
        }
        for fact in related
    ]
