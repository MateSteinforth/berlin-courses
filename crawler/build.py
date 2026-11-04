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
from .util import RAW, SITE_DATA, load_json, load_state, save_json, semester_keys, semester_label

log = logging.getLogger(__name__)

EXAM = re.compile(r"klausur|prüfung|exam\b|modulprüfung|^\s*-|raum anfragen|raumreservierung|platzhalter", re.I)
# admin entries that LSF search listings include (committees, building works, room bookings like "BE 2, 139a")
ADMIN = re.compile(r"sitzung|\bsenat\b|kuratorium|konzil|fakultätsrat|gremi|personalversammlung|alarmtest|"
                   r"rauchmelder|\bumbau\b|semesterferien|raumbuchung|reservierung|\bintern\s*$|^ATD\b|"
                   r"^[A-Z]{1,4} ?\d+[a-z]?, ?\w+\d*\w*$", re.I)
_STOP = {"und", "der", "die", "das", "the", "and", "of", "in", "für", "for", "mit", "with",
         "ii", "i", "iii", "a", "an", "zu", "zur", "zum", "von", "on", "to", "im", "am"}


def _norm_tokens(title: str) -> set[str]:
    t = title.lower()
    t = re.sub(r"\b(wise|ws|sose|ss|wintersemester|sommersemester)\s*\d{2,4}(/\d{2,4})?", " ", t)
    t = re.sub(r"[^\wäöüß]+", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in _STOP and not w.isdigit()}


_PAREN = re.compile(r"\(([^)]*)\)")
LMS_NAME = {"tu_isis": "ISIS", "udk_moodle": "Moodle"}


def _title_parts(c: dict) -> tuple[set[str], set[str]]:
    """Title tokens without parentheticals, and lecturer surname tokens (incl. 'Lektüren (Hauser)')."""
    title = c["title"]
    toks = _norm_tokens(_PAREN.sub(" ", title)) or _norm_tokens(title)
    lect = {w.lower() for name in c.get("lecturers") or [] for w in re.findall(r"\w{3,}", name)}
    for p in _PAREN.findall(title):
        words = re.findall(r"[A-ZÄÖÜ][\wäöüß-]{2,}", p)
        if words and len(words) <= 4 and not re.search(r"\d", p):
            lect |= {w.lower() for w in words}
    return toks, lect


def _link_lms(courses: list[dict], lms: list[dict]) -> set[str]:
    """Attach matching Moodle/ISIS course spaces (best one per LMS) to each catalog course."""
    from .sources.moodle import semester_of
    by_uni_tok: dict[tuple[str, str], list[int]] = defaultdict(list)
    lms_tok, lms_sem, lms_lect = [], [], []
    for i, l in enumerate(lms):
        toks = _norm_tokens(l["title"])
        lms_tok.append(toks)
        lms_sem.append(l.get("semester_hint") or semester_of(l["title"]))
        lms_lect.append({w.lower() for t in l.get("teachers", []) for w in re.findall(r"\w{3,}", t)}
                        | {w.lower() for w in re.findall(r"\w{3,}", l["title"])})
        for t in toks:
            by_uni_tok[(l["university"], t)].append(i)
    used = set()
    for c in courses:
        ct0, lect = _title_parts(c)
        full = _norm_tokens(c["title"])
        best: dict[str, tuple[float, int]] = {}
        # parentheticals are sometimes the lecturer ('Lektüren (Hauser)'), sometimes part of the title
        for ct in {frozenset(ct0), frozenset(full)}:
            if not ct:
                continue
            cand = defaultdict(int)
            for t in ct:
                for i in by_uni_tok.get((c["university"], t), []):
                    cand[i] += 1
            for i, overlap in cand.items():
                lt, sem = lms_tok[i], lms_sem[i]
                if sem and sem != c["semester"]:
                    continue
                same_sem = sem == c["semester"]
                same_lect = bool(lect & lms_lect[i])
                if len(ct) < 2 and not (lt == ct and (same_sem or same_lect)):
                    continue  # one-word titles ('Kolloquium') need an exact match plus semester or lecturer
                score = overlap / len(ct | lt)
                if overlap >= len(ct) * 0.8:  # catalog title fully contained in LMS title
                    score = max(score, 0.75)
                if same_lect:
                    score += 0.2
                if same_sem:
                    score += 0.1
                src = lms[i]["source"]
                if score >= 0.6 and score > best.get(src, (0.0, -1))[0]:
                    best[src] = (score, i)
        for score, i in sorted(best.values(), reverse=True):
            l = lms[i]
            link = {"url": l["url"], "title": l["title"], "source": l["source"]}
            c.setdefault("lms_links", [])
            if all(x["url"] != link["url"] for x in c["lms_links"]):
                c["lms_links"].append(link)
            if not c.get("description") and l.get("summary"):
                c["lms_summary"] = l["summary"]
            used.add(l["id"])
    return used


SYSTEM = {"tu_moses": "MOSES", "udk_hisinone": "HISinOne", "udk_sg": "Studium Generale", "fu_vv": "FU Vorlesungsverzeichnis",
          "hu_agnes": "AGNES", "htw_lsf": "HTW LSF", "ash_lsf": "ASH LSF", "khb_incom": "Incom", "fhp_incom": "Incom"}


def _system(source: str, university: str) -> str:
    return SYSTEM.get(source) or (source.split("_", 1)[-1].upper() if "_" in source else university)


def _link_crosslisted(courses: list[dict]) -> int:
    """Courses offered jointly (FU/HU area studies, UdK/TU Design & Computation, …) appear in
    several catalogs. Same title + same semester + a shared lecturer or time slot -> link them."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    parts = {}
    for c in courses:
        toks, lect = _title_parts(c)
        if len(toks) >= 2:
            parts[c["id"]] = lect
            groups[(c["semester"], frozenset(toks))].append(c)
    n = 0
    for grp in groups.values():
        if len({c["university"] for c in grp}) < 2:
            continue
        for a in grp:
            sa = {(s.get("weekday"), s.get("start")) for s in a.get("sessions", []) if s.get("start")}
            for b in grp:
                if b["university"] == a["university"]:
                    continue
                sb = {(s.get("weekday"), s.get("start")) for s in b.get("sessions", []) if s.get("start")}
                if parts[a["id"]] & parts[b["id"]] or sa & sb:
                    a.setdefault("cross", []).append(b)
                    n += 1
    return n


def _links(c: dict) -> list[dict]:
    """Every place this course is listed, except its own catalog page (that's `url`)."""
    out = []
    seen = {c["url"]}

    def add(label, url, kind):
        if url and url not in seen:
            seen.add(url)
            out.append({"label": label, "url": url, "k": kind})

    for u in c.get("extra", {}).get("also_urls", []):
        add(u["label"], u["url"], "catalog")
    for l in c.get("lms_links", []):
        add(f"{LMS_NAME.get(l['source'], 'Moodle')} course", l["url"], "lms")
    for x in c.get("cross", []):
        add(f"{x['university']} · {_system(x['source'], x['university'])}", x["url"], "catalog")
        for l in x.get("lms_links", []):
            add(f"{LMS_NAME.get(l['source'], 'Moodle')} course ({x['university']})", l["url"], "lms")
    return out


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
            best["extra"].setdefault("also_urls", []).append({"label": "Studium Generale", "url": c["url"]})
            if not best.get("description"):
                best["description"] = c.get("description")
            if not best.get("sessions") and c.get("sessions"):
                best["sessions"] = c["sessions"]
        else:
            out.append(c)
    return out


def build(ai: bool = False, ai_max: int | None = None, ai_unis: list[str] | None = None, ai_wait: int = 120) -> None:
    sems = semester_keys()
    courses: list[dict] = []
    sources_meta = {}
    for f in sorted(RAW.glob("*.json")):
        dump = load_json(f, {})
        if dump.get("kind") != "catalog" or dump.get("semester") not in sems:
            continue
        n = 0
        for c in dump["courses"]:
            if EXAM.search(c.get("type") or "") or EXAM.search(c.get("title") or "") or ADMIN.search(c.get("title") or ""):
                continue
            for s in c.get("sessions", []):
                if s.get("note") and len(s["note"]) > 300:
                    s["note"] = s["note"][:297] + "…"
            courses.append(c)
            n += 1
        sources_meta[f.stem] = {"crawled": dump.get("crawled"), "count": n}

    courses = _merge_studium_generale(courses)
    for c in courses:
        if c.get("extra", {}).get("lms_url"):
            # catalogs often link Moodle's participants page; the course page is what students need
            url = re.sub(r"/user/index\.php\?id=", "/course/view.php?id=", c["extra"]["lms_url"])
            c["lms_links"] = [{"url": url, "title": c["title"], "source": "tu_isis" if "isis." in url else "moodle"}]

    lms: list[dict] = []
    for f in sorted(RAW.glob("*.json")):
        dump = load_json(f, {})
        if dump.get("kind") == "lms":
            lms.extend(dump["courses"])
            sources_meta[f.stem] = {"crawled": dump.get("crawled"), "count": len(dump["courses"])}
    used = _link_lms(courses, lms) if lms else set()
    cross = _link_crosslisted(courses)
    log.info("build: %d catalog courses, %d LMS spaces, %d linked, %d cross-listings",
             len(courses), len(lms), len(used), cross)

    # cached AI results for everyone; new API calls only with --ai (optionally limited to some universities)
    from .enrich import enrich
    import os
    key = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        ai_data = enrich(courses)  # cache hits only
    finally:
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    if ai:
        subset = [c for c in courses if not ai_unis or c["university"] in ai_unis]
        log.info("AI: enrichment for %s (%d courses)", ", ".join(ai_unis) if ai_unis else "all universities", len(subset))
        ai_data.update(enrich(subset, max_new=ai_max, wait_min=ai_wait))

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
            "sys": _system(c["source"], c["university"]),
            "links": _links(c) or None,
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
        "next_update": min((v["next_update"] for v in load_state().values()),
                           default=None),
    }
    save_json(SITE_DATA / "courses.json", out, compact=True)
    save_json(SITE_DATA / "details.json", details, compact=True)
    save_json(SITE_DATA / "locations.json", locations, compact=True)
    save_json(SITE_DATA / "meta.json", meta)
    log.info("build: wrote %d courses (%d with AI tags), %d locations", len(out), meta["ai"], len(locations))
