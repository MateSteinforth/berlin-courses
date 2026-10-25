"""Hand-maintained courses for schools without a public online catalog
(e.g. HfS Ernst Busch, whose timetable lives behind a login).

Add a file data/manual/<uni>_<semester>.json:

  {"university": "HfS", "semester": "2026W", "source_url": "...",
   "courses": [
     {"title": "…", "type": "Workshop", "lecturers": ["…"], "language": "Deutsch",
      "description": "…", "url": "…",
      "dates_text": "Sa/So 7./8.11.2026, 10–17 Uhr, Zinnowitzer Str. 11",   # free text, parsed by Haiku
      "sessions": [ {"weekday": 1, "start": "10:00", "end": "13:00", "rhythm": "weekly",
                     "first_date": "2026-10-13", "last_date": "2027-02-09",
                     "room": "…", "building": "ADDR:Zinnowitzer Straße 11"} ] }
   ]}

`sessions` is optional; if missing, `dates_text` is turned into sessions by
Claude Haiku (cached). Every file is one semester of one school.
"""
from __future__ import annotations

import hashlib
import logging

from ..model import Course, Session
from ..util import DATA, load_json, semester_label
from . import CatalogSource

log = logging.getLogger(__name__)
MANUAL = DATA / "manual"

# default building per school (single-campus schools)
CAMPUS = {"HfS": "ADDR:Zinnowitzer Straße 11"}


class Manual(CatalogSource):
    name = "manual"
    university = "manual"

    def next_update(self, semester, after, published=True):
        from datetime import timedelta
        return after + timedelta(days=1)  # local files: cheap, re-read daily

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        out: list[Course] = []
        for f in sorted(MANUAL.glob(f"*_{semester}.json")):
            d = load_json(f, {})
            uni = d.get("university") or f.stem.split("_")[0]
            todo = []
            for i, c in enumerate(d.get("courses", [])[: limit or None]):
                cid = hashlib.sha1(f"{uni}{c.get('title')}".encode()).hexdigest()[:10]
                sessions = [Session(**s) for s in c.get("sessions", [])]
                course = Course(
                    id=f"manual:{uni}:{cid}:{semester}", source=f"manual_{uni.lower()}", university=uni,
                    semester=semester, title=c["title"], url=c.get("url") or d.get("source_url", ""),
                    type=c.get("type"), lecturers=c.get("lecturers", []), org=c.get("org"),
                    language=c.get("language"), description=c.get("description"),
                    sessions=sessions, catalog_path=c.get("path", []),
                    extra={"manual": True},
                )
                if not sessions and c.get("dates_text"):
                    todo.append((course, c["dates_text"]))
                out.append(course)
            if todo:
                from ..enrich import extract_sessions
                got = extract_sessions([(c.id, t, semester_label(semester)) for c, t in todo])
                for c, _ in todo:
                    c.sessions = [Session(**s) for s in got.get(c.id, [])]
                    for s in c.sessions:
                        s.building = s.building or CAMPUS.get(uni)
            log.info("manual %s: %d courses from %s", uni, len(d.get("courses", [])), f.name)
        for c in out:
            c.by_arrangement = not c.sessions
        return out
