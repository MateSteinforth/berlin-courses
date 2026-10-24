"""Merge raw source dumps into the files the frontend loads.

site/data/courses.json   – compact course list (everything the filters need)
site/data/details.json   – id -> long description (lazy-loaded on click)
site/data/locations.json – building key -> {name, address, lat, lon}
site/data/meta.json      – build time, semesters, sources, counts
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

from .locations import resolve
from .util import RAW, SITE_DATA, load_json, save_json, semester_keys, semester_label

log = logging.getLogger(__name__)

EXAM = re.compile(r"klausur|prüfung|exam\b|modulprüfung|^\s*-|raum anfragen|raumreservierung|platzhalter", re.I)
_STOP = {"und", "der", "die", "das", "the", "and", "of", "in", "für", "for", "mit", "with",
         "ii", "i", "iii", "a", "an", "zu", "zur", "zum", "von", "on", "to", "im", "am"}


def _norm_tokens(title: str) -> set[str]:
    t = title.lower()
    t = re.sub(r"\b(wise|ws|sose|ss|wintersemester|sommersemester)\s*\d{2,4}(/\d{2,4})?", " ", t)
    t = re.sub(r"[^\wäöüß]+", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in _STOP and not w.isdigit()}


def _link_lms(courses: list[dict], lms: list[dict]) -> set[str]:
    """Attach the best-matching Moodle/ISIS course space to each catalog course."""
    by_uni_tok: dict[tuple[str, str], list[int]] = defaultdict(list)
    lms_tok = []
    for i, l in enumerate(lms):
        toks = _norm_tokens(l["title"])
        lms_tok.append(toks)
        for t in toks:
            by_uni_tok[(l["university"], t)].append(i)
    used = set()
    for c in courses:
        ct = _norm_tokens(c["title"])
        if len(ct) < 2:
            continue
        cand = defaultdict(int)
        for t in ct:
            for i in by_uni_tok.get((c["university"], t), []):
                cand[i] += 1
        best, best_score = None, 0.0
        lect = {w.lower() for name in c.get("lecturers", []) for w in re.findall(r"\w{3,}", name)}
        for i, overlap in cand.items():
            lt = lms_tok[i]
            score = overlap / len(ct | lt)
            if overlap >= len(ct) * 0.8:  # catalog title fully contained in LMS title
                score = max(score, 0.75)
            l = lms[i]
            if l.get("semester_hint") and l["semester_hint"] != c["semester"]:
                continue
            if lect and lect & {w.lower() for t in l.get("teachers", []) for w in re.findall(r"\w{3,}", t)}:
                score += 0.2
            if l.get("semester_hint") == c["semester"]:
                score += 0.1
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= 0.6:
            l = lms[best]
            c["lms"] = {"url": l["url"], "title": l["title"], "source": l["source"]}
            if not c.get("description") and l.get("summary"):
                c["lms_summary"] = l["summary"]
            used.add(l["id"])
    return used


def _merge_studium_generale(courses: list[dict]) -> list[dict]:
    """Studium Generale pages often describe courses that are also in HISinOne.
    Flag the HISinOne entry and drop the duplicate; keep SG-only courses."""
    udk = [c for c in courses if c["source"] == "udk_hisinone"]
    tok = [(_norm_tokens(c["title"]), c) for c in udk]
    out = []
    for c in courses:
        if c["source"] != "udk_sg":
            out.append(c)
            continue
        ct = _norm_tokens(c["title"])
        best, score = None, 0.0
        for t, u in tok:
            if u["semester"] != c["semester"] or not t or not ct:
                continue
            j = len(ct & t) / len(ct | t)
            if j > score:
                best, score = u, j
        if best and score >= 0.6:
            best.setdefault("extra", {})["studium_generale"] = True
            if not best.get("description"):
                best["description"] = c.get("description")
            if not best.get("sessions") and c.get("sessions"):
                best["sessions"] = c["sessions"]
        else:
            out.append(c)
    return out


def build(ai: bool = False, ai_max: int | None = None) -> None:
    sems = semester_keys()
    courses: list[dict] = []
    sources_meta = {}
    for f in sorted(RAW.glob("*.json")):
        dump = load_json(f, {})
        if dump.get("kind") != "catalog" or dump.get("semester") not in sems:
            continue
        n = 0
        for c in dump["courses"]:
            if EXAM.search(c.get("type") or "") or EXAM.search(c.get("title") or ""):
                continue
            for s in c.get("sessions", []):
                if s.get("note") and len(s["note"]) > 300:
                    s["note"] = s["note"][:297] + "…"
            courses.append(c)
            n += 1
        sources_meta[f.stem] = {"crawled": dump.get("crawled"), "count": n}

    courses = _merge_studium_generale(courses)
    for c in courses:
        if c.get("extra", {}).get("lms_url") and not c.get("lms"):
            c["lms"] = {"url": c["extra"]["lms_url"], "title": c["title"], "source": c["source"]}

    lms: list[dict] = []
    for f in sorted(RAW.glob("*.json")):
        dump = load_json(f, {})
        if dump.get("kind") == "lms":
            lms.extend(dump["courses"])
            sources_meta[f.stem] = {"crawled": dump.get("crawled"), "count": len(dump["courses"])}
    used = _link_lms(courses, lms) if lms else set()
    log.info("build: %d catalog courses, %d LMS spaces, %d linked", len(courses), len(lms), len(used))

    ai_data = {}
    if ai:
        from .enrich import enrich
        ai_data = enrich(courses, max_new=ai_max)
    else:
        from .enrich import enrich as _cached_only
        import os
        key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ai_data = _cached_only(courses)  # cache hits only
        finally:
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key

    keys = {s["building"] for c in courses for s in c.get("sessions", []) if s.get("building")}
    locations = resolve(keys)

    out, details = [], {}
    for c in courses:
        a = ai_data.get(c["id"], {})
        desc = c.get("description") or c.get("lms_summary")
        rec = {
            "id": c["id"],
            "u": c["university"],
            "sem": c["semester"],
            "t": c["title"],
            "url": c["url"],
            "ty": c.get("type"),
            "nr": c.get("number"),
            "lec": c.get("lecturers") or None,
            "org": c.get("org"),
            "lang": c.get("language"),
            "path": c.get("catalog_path") or None,
            "arr": c.get("by_arrangement") or None,
            "lms": c.get("lms"),
            "sg": c.get("extra", {}).get("studium_generale") or None,
            "ss": [{k: v for k, v in {
                "d": s.get("weekday"), "s": s.get("start"), "e": s.get("end"),
                "r": s.get("rhythm"), "f": s.get("first_date"), "l": s.get("last_date"),
                "room": s.get("room"), "b": s.get("building") if s.get("building") in locations else None,
                "g": s.get("group"), "n": s.get("note"),
            }.items() if v not in (None, "")} for s in c.get("sessions", [])] or None,
            "sum": a.get("summary"),
            "tags": a.get("tags") or None,
            "kw": a.get("keywords") or None,
            "ail": a.get("language"),
            "lvl": a.get("level"),
            "rel": a.get("dc_relevance"),
            "kind": a.get("kind"),
            "hasd": bool(desc) or None,
        }
        out.append({k: v for k, v in rec.items() if v is not None})
        if desc:
            details[c["id"]] = desc[:6000]

    meta = {
        "built": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "semesters": [{"key": s, "label": semester_label(s),
                       "count": sum(1 for c in out if c["sem"] == s)} for s in sems],
        "sources": sources_meta,
        "count": len(out),
        "ai": sum(1 for c in out if "tags" in c),
        "next_update": min((v["next_update"] for v in load_json(RAW.parent / "state.json", {}).values()),
                           default=None),
    }
    save_json(SITE_DATA / "courses.json", out, compact=True)
    save_json(SITE_DATA / "details.json", details, compact=True)
    save_json(SITE_DATA / "locations.json", locations, compact=True)
    save_json(SITE_DATA / "meta.json", meta)
    log.info("build: wrote %d courses (%d with AI tags), %d locations", len(out), meta["ai"], len(locations))
