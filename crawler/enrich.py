"""AI enrichment with Claude Haiku: English summary, topic tags, D+C relevance.

Results are cached by a hash of the course's text, so each run only sends new
or changed courses. Without ANTHROPIC_API_KEY this step is skipped and the
site still works (just without tags/summaries).

Cost: courses go out through the Message Batches API (50% off), GROUP courses
per request (the instructions are sent once per group instead of once per
course) and with thinking off (plain classification doesn't need it).
Unfinished batches are remembered in ai_batches.json and collected by the next
run, so a slow batch never gets paid for twice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .util import CACHE, env, load_json, save_json

log = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
CACHE_FILE = CACHE / "ai.json"
BATCH_FILE = CACHE / "ai_batches.json"   # pending batch id -> {custom_id: [cache keys]}
GROUP = 40                               # courses per request
BATCH_WAIT_MIN = 120                     # how long a run waits for a batch before leaving it for the next run
PROMPT_VERSION = "v2"

TAGS = [
    "art", "design", "architecture", "urbanism", "music", "sound", "performance",
    "dance", "theatre", "film-video", "photography", "media-theory", "programming",
    "creative-coding", "ai-ml", "data", "hci-interaction", "electronics-hardware",
    "robotics", "fabrication-making", "materials", "textiles-fashion", "games",
    "xr-3d", "sustainability", "society-politics", "philosophy-theory", "history",
    "economics-business", "engineering", "mathematics", "natural-sciences",
    "writing-language", "education-teaching", "workshop-practice", "career-professional",
]

# Output per course, with one-letter keys to keep output tokens down (expanded to SHORT's values in the cache).
SHORT = {"s": "summary", "t": "tags", "l": "language", "r": "dc_relevance"}
SCHEMA = {
    "type": "object",
    "properties": {
        "s": {"type": "string"},
        "t": {"type": "array", "items": {"type": "string", "enum": TAGS}},
        "l": {"type": "string", "enum": ["de", "en", "de+en", "other", "unknown"]},
        "r": {"type": "integer", "enum": [0, 1, 2, 3]},
    },
    "required": ["s", "t", "l", "r"],
    "additionalProperties": False,
}

SYSTEM = (
    "You classify courses from Berlin/Potsdam universities for a course finder used by students of "
    "the Master 'Design & Computation' (UdK/TU: architecture, design, art, computation, digital "
    "fabrication, interaction, AI, critical theory). For each course return:\n"
    "s: ONE short plain English sentence on what it covers (max 25 words, no 'This course').\n"
    "t: 1-3 tags from the fixed list.\n"
    "l: teaching language if stated or clearly implied, else 'unknown'.\n"
    "r: relevance for Design & Computation students: 0 unrelated, 1 tangential, 2 relevant, 3 core.\n"
    "If a record is sparse, infer cautiously from title and department."
)

DESC_CHARS = 1000  # enough to classify and summarize; long module texts were most of the input cost


def _text(c: dict) -> str:
    desc = " ".join((c.get("description") or c.get("lms_summary") or "").split())[:DESC_CHARS]
    parts = [
        f"{c.get('university')} | {c.get('title')} | {c.get('type') or ''}",
        f"Dept: {c.get('org') or ' > '.join((c.get('catalog_path') or [])[-2:])}",
        f"Lang: {c.get('language')}" if c.get("language") else "",
        f"Desc: {desc}" if desc else "",
    ]
    return "\n".join(p for p in parts if p)


def _key(c: dict) -> str:
    return hashlib.sha1((PROMPT_VERSION + _text(c)).encode()).hexdigest()[:16]


def enrich(courses: list[dict], max_new: int | None = None, wait_min: int = BATCH_WAIT_MIN) -> dict[str, dict]:
    """Return {course_id: ai_fields}; calls the API only for uncached courses."""
    cache = load_json(CACHE_FILE, {})
    out, todo = {}, []
    for c in courses:
        k = _key(c)
        if k in cache:
            out[c["id"]] = cache[k]
        else:
            todo.append((c, k))
    if not env("ANTHROPIC_API_KEY"):
        if todo:
            log.info("AI: %d courses need enrichment but ANTHROPIC_API_KEY is not set – skipping", len(todo))
        return out
    if not todo and not load_json(BATCH_FILE, {}):
        return out
    if max_new is not None:
        todo = todo[:max_new]

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic(max_retries=5)
    pending = load_json(BATCH_FILE, {})
    in_flight = {k for groups in pending.values() for keys in groups.values() for k in keys}
    todo = [(c, k) for c, k in todo if k not in in_flight]

    if todo:
        # one request per GROUP courses; duplicate texts (same course listed twice) are sent once
        uniq = list({k: c for c, k in todo}.items())
        groups = [uniq[i:i + GROUP] for i in range(0, len(uniq), GROUP)]
        reqs, mapping = [], {}
        for gi, grp in enumerate(groups):
            cid = f"g{gi}"
            mapping[cid] = [k for k, _ in grp]
            body = "\n\n".join(f"### Course {i}\n{_text(c)}" for i, (_, c) in enumerate(grp))
            reqs.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=150 * len(grp) + 300,
                system=SYSTEM + GROUP_NOTE,
                messages=[{"role": "user", "content": body}],
                thinking={"type": "disabled"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": GROUP_SCHEMA}},
            )))
        batch = client.messages.batches.create(requests=reqs)
        pending[batch.id] = mapping
        save_json(BATCH_FILE, pending)
        log.info("AI: batch %s submitted: %d courses in %d requests", batch.id, len(uniq), len(reqs))

    _collect_batches(client, pending, cache, wait_min=wait_min)
    for c in courses:
        k = _key(c)
        if k in cache:
            out[c["id"]] = cache[k]
    return out


GROUP_NOTE = (
    "\n\nThe message contains several course records, each starting with '### Course <n>'. "
    "Return one entry per course in `results`, with `n` set to that course's number."
)
GROUP_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        **SCHEMA,
        "properties": {"n": {"type": "integer"}, **SCHEMA["properties"]},
        "required": ["n", *SCHEMA["required"]],
    }}},
    "required": ["results"],
    "additionalProperties": False,
}


def _collect_batches(client, pending: dict, cache: dict, wait_min: int) -> None:
    """Poll pending batches up to wait_min minutes; store finished results in the cache.
    Batches still running stay in ai_batches.json for the next run."""
    deadline = time.time() + wait_min * 60
    while pending:
        for bid in list(pending):
            b = client.messages.batches.retrieve(bid)
            if b.processing_status != "ended":
                continue
            got = 0
            for r in client.messages.batches.results(bid):
                keys = pending[bid].get(r.custom_id, [])
                if r.result.type != "succeeded":
                    log.warning("AI: request %s %s – retried next run", r.custom_id, r.result.type)
                    continue
                msg = r.result.message
                if msg.stop_reason in ("refusal", "max_tokens"):
                    log.warning("AI: request %s stopped (%s) – retried next run", r.custom_id, msg.stop_reason)
                    continue
                text = next((x.text for x in msg.content if x.type == "text"), None)
                for item in (json.loads(text).get("results", []) if text else []):
                    n = item.pop("n", None)
                    if isinstance(n, int) and 0 <= n < len(keys):
                        cache[keys[n]] = {SHORT[k]: v for k, v in item.items() if k in SHORT}
                        got += 1
            del pending[bid]
            save_json(CACHE_FILE, cache)
            save_json(BATCH_FILE, pending)
            log.info("AI: batch %s collected (%d courses; %s)", bid, got, b.request_counts)
        if not pending:
            break
        if time.time() > deadline:
            log.info("AI: %d batch(es) still running – results are collected by the next run", len(pending))
            break
        time.sleep(60)


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
