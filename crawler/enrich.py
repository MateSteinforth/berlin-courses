"""AI enrichment with Claude Haiku: English summary, topic tags, D+C relevance.

Results are cached by a hash of the course's text, so each run only sends new
or changed courses. Without ANTHROPIC_API_KEY this step is skipped and the
site still works (just without tags/summaries).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .util import CACHE, env, load_json, save_json

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
CACHE_FILE = CACHE / "ai.json"
PROMPT_VERSION = "v1"

TAGS = [
    "art", "design", "architecture", "urbanism", "music", "sound", "performance",
    "dance", "theatre", "film-video", "photography", "media-theory", "programming",
    "creative-coding", "ai-ml", "data", "hci-interaction", "electronics-hardware",
    "robotics", "fabrication-making", "materials", "textiles-fashion", "games",
    "xr-3d", "sustainability", "society-politics", "philosophy-theory", "history",
    "economics-business", "engineering", "mathematics", "natural-sciences",
    "writing-language", "education-teaching", "workshop-practice", "career-professional",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "1-2 sentence English summary of what the course is about"},
        "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string", "enum": ["de", "en", "de+en", "other", "unknown"]},
        "level": {"type": "string", "enum": ["intro", "intermediate", "advanced", "any"]},
        "dc_relevance": {"type": "integer", "enum": [0, 1, 2, 3]},
        "kind": {"type": "string", "enum": ["lecture", "seminar", "project", "workshop", "lab",
                                            "exercise", "colloquium", "exam", "individual", "other"]},
    },
    "required": ["summary", "tags", "keywords", "language", "level", "dc_relevance", "kind"],
    "additionalProperties": False,
}

SYSTEM = (
    "You classify courses from Berlin and Potsdam universities and art schools (TU, UdK, FU, HU, HTW, ASH, "
    "weißensee, FH Potsdam, …) for a "
    "course finder used by students of the Master 'Design & Computation' (a joint UdK/TU program "
    "spanning architecture, design, art, computation, digital fabrication, interaction, AI and "
    "critical theory). Given a course record, return:\n"
    "- summary: 1-2 plain English sentences on what the course covers (no marketing, no 'This course').\n"
    "- tags: 1-5 from the fixed list that best describe the subject.\n"
    "- keywords: up to 5 short specific English keywords (methods, tools, topics).\n"
    "- language: teaching language if stated or clearly implied by the text, else 'unknown'.\n"
    "- level: intro / intermediate / advanced / any.\n"
    "- dc_relevance: 0 unrelated, 1 tangential, 2 relevant, 3 core interest for Design & Computation students.\n"
    "- kind: the course format.\n"
    "If the record is sparse, infer cautiously from the title and department."
)


def _text(c: dict) -> str:
    parts = [
        f"University: {c.get('university')}",
        f"Title: {c.get('title')}",
        f"Type: {c.get('type') or ''}",
        f"Department: {c.get('org') or ''}",
        f"Catalog path: {' > '.join(c.get('catalog_path') or [])}",
        f"Lecturers: {', '.join(c.get('lecturers') or [])}",
        f"Language (as published): {c.get('language') or ''}",
        f"Description: {(c.get('description') or c.get('lms_summary') or '')[:3000]}",
    ]
    return "\n".join(parts)


def _key(c: dict) -> str:
    return hashlib.sha1((PROMPT_VERSION + _text(c)).encode()).hexdigest()[:16]


def enrich(courses: list[dict], max_new: int | None = None) -> dict[str, dict]:
    """Return {course_id: ai_fields}; calls the API only for uncached courses."""
    cache = load_json(CACHE_FILE, {})
    out, todo = {}, []
    for c in courses:
        k = _key(c)
        if k in cache:
            out[c["id"]] = cache[k]
        else:
            todo.append((c, k))
    if not todo:
        return out
    if not env("ANTHROPIC_API_KEY"):
        log.info("AI: %d courses need enrichment but ANTHROPIC_API_KEY is not set – skipping", len(todo))
        return out
    if max_new is not None:
        todo = todo[:max_new]

    import anthropic

    client = anthropic.Anthropic(max_retries=5)
    lock = threading.Lock()
    done = 0

    def one(item):
        nonlocal done
        c, k = item
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM,
                messages=[{"role": "user", "content": _text(c)}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except anthropic.RateLimitError as e:
            log.warning("AI rate limited on %s: %s", c["id"], e)
            return
        except anthropic.APIStatusError as e:
            log.warning("AI API error %s on %s: %s", e.status_code, c["id"], e.message)
            return
        except anthropic.APIConnectionError as e:
            log.warning("AI connection error on %s: %s", c["id"], e)
            return
        if resp.stop_reason in ("refusal", "max_tokens"):
            log.warning("AI %s on %s", resp.stop_reason, c["id"])
            return
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            return
        data = json.loads(text)
        with lock:
            cache[k] = data
            out[c["id"]] = data
            done += 1
            if done % 200 == 0:
                log.info("AI: %d/%d", done, len(todo))
                save_json(CACHE_FILE, cache)

    log.info("AI: enriching %d courses with %s", len(todo), MODEL)
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, todo))
    save_json(CACHE_FILE, cache)
    log.info("AI: done (%d new)", done)
    return out


# ---------------------------------------------------------------------------
# Free-text dates -> structured sessions (e.g. UdK Studium Generale pages)

SESS_CACHE = CACHE / "ai_sessions.json"
SESS_SCHEMA = {
    "type": "object",
    "properties": {
        "sessions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "weekday": {"type": ["integer", "null"], "description": "0=Mon..6=Sun for weekly series or single dates"},
                    "start": {"type": ["string", "null"], "description": "HH:MM"},
                    "end": {"type": ["string", "null"], "description": "HH:MM"},
                    "rhythm": {"type": "string", "enum": ["weekly", "biweekly", "single", "block", "irregular"]},
                    "first_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "last_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "room": {"type": ["string", "null"], "description": "room / venue as written"},
                },
                "required": ["weekday", "start", "end", "rhythm", "first_date", "last_date", "room"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sessions"],
    "additionalProperties": False,
}
SESS_SYSTEM = (
    "Extract the meeting dates of a university course from the start of its web page. "
    "Return one entry per distinct meeting pattern: a weekly series (weekday + time + first/last date), "
    "a block of consecutive days (rhythm 'block', first_date..last_date, weekday null), or single dates "
    "(rhythm 'single', one entry per date). Times as HH:MM ('11-17 h' -> 11:00/17:00). "
    "Use the semester given to resolve years. If no dates are stated, return an empty list. "
    "Never invent dates."
)


def extract_sessions(items: list[tuple[str, str, str]]) -> dict[str, list[dict]]:
    """items: (id, raw_text, semester_label) -> {id: [session dicts]}"""
    cache = load_json(SESS_CACHE, {})
    out, todo = {}, []
    for cid, text, sem in items:
        k = hashlib.sha1((sem + text).encode()).hexdigest()[:16]
        if k in cache:
            out[cid] = cache[k]
        else:
            todo.append((cid, text, sem, k))
    if todo and not env("ANTHROPIC_API_KEY"):
        log.info("AI: %d free-text date blocks need extraction but no ANTHROPIC_API_KEY", len(todo))
        return out
    if not todo:
        return out
    import anthropic
    client = anthropic.Anthropic(max_retries=5)
    lock = threading.Lock()

    def one(item):
        cid, text, sem, k = item
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=4096, system=SESS_SYSTEM,
                messages=[{"role": "user", "content": f"Semester: {sem}\n\n{text}"}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SESS_SCHEMA}},
            )
        except anthropic.APIError as e:
            log.warning("AI session extraction failed for %s: %s", cid, e)
            return
        if resp.stop_reason in ("refusal", "max_tokens"):
            return
        txt = next((b.text for b in resp.content if b.type == "text"), None)
        if not txt:
            return
        ss = json.loads(txt)["sessions"]
        with lock:
            cache[k] = ss
            out[cid] = ss

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(one, todo))
    save_json(SESS_CACHE, cache)
    return out
