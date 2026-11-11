import json
import re
from typing import Any

from app.config import ANTHROPIC_API_KEY
from app.database import get_connection

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None


ALLOWED_RELATIONS = {
    "LAUNCHED",
    "ANNOUNCED",
    "PARTNERED_WITH",
    "INTRODUCED",
    "HAS_DETAIL",
    "RELATED_TO",
}


def normalize_relation(raw: str) -> str:
    cleaned = raw.strip().upper().replace(" ", "_")
    if cleaned in ALLOWED_RELATIONS:
        return cleaned
    return ""


def _topic_for_object(value: str) -> str:
    lowered = value.lower()
    if "credit card" in lowered or "card" in lowered:
        return "Credit Cards"
    if "scheme" in lowered or "grant" in lowered or "fellowship" in lowered:
        return "Schemes"
    if "regulation" in lowered or "notice" in lowered or "policy" in lowered:
        return "Regulations"
    return "General"


def _clean_subject(value: str) -> str:
    cleaned = re.sub(r"^at launch,?\s+", "", value.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned)
    if cleaned.casefold().startswith(("launch was ", "following ")):
        return ""
    return cleaned.strip(" .,:;|")


def _clean_object(value: str) -> str:
    cleaned = re.sub(r"^(?:the|a|an|its|their)\s+", "", value.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(?:on|in)\s+\d{1,2}\s+[A-Za-z]+\s+\d{4}.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(?:on|in)\s+[A-Za-z]+\s+\d{4}.*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .,:;")


def _extract_sentence_facts(text: str, document_id: int, page_number: int) -> list[dict]:
    facts: list[dict] = []
    relation_pattern = re.compile(
        r"(?P<subject>[A-Z][A-Za-z0-9&'(). -]{1,70}?)\s+"
        r"(?P<verb>launched|launches|announced|announces|introduced|introduces)\s+"
        r"(?P<object>[^.;\n]+)",
        flags=re.IGNORECASE,
    )
    partnership_pattern = re.compile(
        r"(?P<subject>[A-Z][A-Za-z0-9&'(). -]{1,70}?)\s+"
        r"partnered with\s+"
        r"(?P<object>[A-Z][A-Za-z0-9&'(). -]{1,70})",
        flags=re.IGNORECASE,
    )
    detail_pattern = re.compile(
        r"(?P<subject>[A-Z][A-Za-z0-9&'(). -]{2,70}?)\s+"
        r"(?P<verb>offered|offers|includes|included|requires|required|was available to|is available to)\s+"
        r"(?P<object>[^.;\n]+)",
        flags=re.IGNORECASE,
    )

    logical_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        previous = logical_lines[-1] if logical_lines else ""
        continues = bool(previous) and not re.search(r"[.!?][\"')]*$", previous)
        month_wrap = bool(re.search(r"\bon\s+\d+$", previous, re.IGNORECASE)) and bool(
            re.match(
                r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\b",
                line,
                re.IGNORECASE,
            )
        )
        continuation_word = bool(
            re.match(r"[a-z0-9]", line)
            or re.search(r"\b(?:with|and|of|by|for|in|to)$", previous, re.IGNORECASE)
        )
        if continues and (month_wrap or continuation_word):
            logical_lines[-1] = f"{previous} {line}"
        else:
            logical_lines.append(line)

    for logical_line in logical_lines:
        logical_line = re.sub(r"^\d{1,2}\s*\|\s*[^|]+\|\s*", "", logical_line)
        logical_line = re.sub(r"^\d{1,2}\s*\|\s*[^|]+\s+", "", logical_line)
        sentences = re.split(r"(?<=[.!?])\s+", logical_line)
        for line in sentences:
            for pattern in (relation_pattern, partnership_pattern, detail_pattern):
                for match in pattern.finditer(line):
                    subject = _clean_subject(match.group("subject"))
                    obj = _clean_object(match.group("object"))
                    if not subject or not obj:
                        continue
                    if "verb" in match.groupdict():
                        verb = match.group("verb").lower()
                        relation = (
                            "LAUNCHED" if verb.startswith("launch")
                            else "ANNOUNCED" if verb.startswith("announc")
                            else "INTRODUCED" if verb.startswith("introduc")
                            else "HAS_DETAIL"
                        )
                    else:
                        relation = "PARTNERED_WITH"
                    if subject.casefold() == obj.casefold():
                        continue
                    facts.append({
                        "document_id": document_id,
                        "subject": subject,
                        "relation": relation,
                        "object": obj,
                        "topic": _topic_for_object(f"{subject} {obj}"),
                        "page_number": page_number,
                        "evidence": line,
                    })

    deduplicated = []
    seen = set()
    for fact in facts:
        key = (fact["subject"].casefold(), fact["relation"], fact["object"].casefold())
        if key not in seen:
            seen.add(key)
            deduplicated.append(fact)
    return deduplicated


def fallback_extract_facts(passages: list[dict], document_id: int) -> list[dict]:
    facts: list[dict] = []
    for passage in passages:
        facts.extend(
            _extract_sentence_facts(
                passage.get("text", ""),
                document_id,
                int(passage["page_number"]),
            )
        )
    return facts


def _source_supports_fact(fact: dict, passages: list[dict]) -> bool:
    page_number = fact.get("page_number")
    evidence = re.sub(r"\s+", " ", str(fact.get("evidence", ""))).strip()
    if not evidence:
        return False
    for passage in passages:
        if passage.get("page_number") != page_number:
            continue
        source_text = re.sub(r"\s+", " ", str(passage.get("text", ""))).strip()
        if evidence.casefold() not in source_text.casefold():
            return False
        source_lower = source_text.casefold()
        relation_evidence = {
            "LAUNCHED": ("launch", "introduced"),
            "ANNOUNCED": ("announc",),
            "PARTNERED_WITH": ("partner", "in partnership"),
            "INTRODUCED": ("introduc",),
            "HAS_DETAIL": ("offered", "includes", "required", "available", "amount was"),
            "RELATED_TO": ("related to", "associated with"),
        }.get(str(fact.get("relation", "")).upper(), ())
        return (
            str(fact.get("subject", "")).casefold() in source_lower
            and str(fact.get("object", "")).casefold() in source_lower
            and any(term in evidence.casefold() for term in relation_evidence)
        )
    return False


def extract_facts(passages: list[dict], document_id: int) -> list[dict]:
    if not passages:
        return []
    if not ANTHROPIC_API_KEY or Anthropic is None:
        return fallback_extract_facts(passages, document_id)

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = {
        "system": "You are extracting facts from uploaded PDF text. Return ONLY a JSON array with objects containing subject, relation, object, topic, evidence, page_number. Keep facts strictly grounded in the source text. If there is no trustworthy fact, return an empty array.",
        "messages": [
            {
                "role": "user",
                "content": json.dumps(
                    [{"page_number": item["page_number"], "text": item["text"]} for item in passages]
                ),
            }
        ],
    }

    response = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=800,
        messages=prompt["messages"],
        system=prompt["system"],
    )

    raw_text = response.content[0].text
    raw_json = raw_text.strip()
    if raw_json.startswith("```"):
        raw_json = raw_json.strip("`\n ")
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return fallback_extract_facts(passages, document_id)

    facts = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        subject = _clean_subject(str(item.get("subject", "")))
        obj = str(item.get("object", "")).strip()
        relation = normalize_relation(str(item.get("relation", "RELATED_TO")))
        topic = str(item.get("topic", obj or "General")).strip() or "General"
        evidence = str(item.get("evidence", "")).strip()
        try:
            page_number = int(item.get("page_number", 1))
        except (TypeError, ValueError):
            continue
        candidate = {
                "document_id": document_id,
                "subject": subject,
                "relation": relation,
                "object": obj,
                "topic": topic,
                "page_number": page_number,
                "evidence": evidence,
            }
        if (
            subject
            and obj
            and relation in ALLOWED_RELATIONS
            and 1 <= page_number <= max(int(p["page_number"]) for p in passages)
            and _source_supports_fact(candidate, passages)
        ):
            facts.append(candidate)
    return facts or fallback_extract_facts(passages, document_id)
