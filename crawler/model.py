"""Normalized data model shared by all sources.

Every source plugin produces a list of `Course` objects. The build step merges
them, attaches locations, AI tags and LMS links, and writes the JSON the
frontend reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

# Rhythm values used across sources
WEEKLY = "weekly"
BIWEEKLY = "biweekly"
SINGLE = "single"
BLOCK = "block"
DAILY = "daily"
IRREGULAR = "irregular"


@dataclass
class Session:
    """One recurring (or single) meeting of a course."""
    weekday: Optional[int]          # 0=Mon … 6=Sun, None if unknown (e.g. block without weekday)
    start: Optional[str]            # "HH:MM"
    end: Optional[str]              # "HH:MM"
    rhythm: str = WEEKLY
    first_date: Optional[str] = None  # ISO date
    last_date: Optional[str] = None   # ISO date
    room: Optional[str] = None        # raw room string as published
    building: Optional[str] = None    # normalized building code, e.g. "TU:H", "UDK:Ha33"
    group: Optional[str] = None       # parallel group ("Termingruppe 1"); a course fits if ANY group fits
    note: Optional[str] = None


@dataclass
class Course:
    id: str                          # globally unique: "<source>:<native id>:<semester>"
    source: str                      # plugin name, e.g. "tu_moses"
    university: str                  # "TU" | "UdK" | ...
    semester: str                    # "2026W" | "2027S"
    title: str
    url: str
    type: Optional[str] = None       # Vorlesung, Seminar, Projekt, …
    number: Optional[str] = None     # LV-Nummer
    lecturers: list[str] = field(default_factory=list)
    org: Optional[str] = None        # faculty / institute / Fachgebiet
    language: Optional[str] = None   # as published ("Deutsch", "Englisch", …)
    description: Optional[str] = None
    sessions: list[Session] = field(default_factory=list)
    by_arrangement: bool = False     # "nach Vereinbarung"
    catalog_path: list[str] = field(default_factory=list)  # breadcrumb in the source catalog
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, [], {}, "")}

    @staticmethod
    def from_dict(d: dict) -> "Course":
        d = dict(d)
        d["sessions"] = [Session(**s) for s in d.get("sessions", [])]
        known = Course.__dataclass_fields__.keys()
        return Course(**{k: v for k, v in d.items() if k in known})


@dataclass
class LmsCourse:
    """A course space in a learning management system (Moodle/ISIS)."""
    id: str
    source: str
    university: str
    title: str
    url: str
    category: list[str] = field(default_factory=list)
    summary: Optional[str] = None
    teachers: list[str] = field(default_factory=list)
    semester_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {}, "")}


def sessions_from_dates(dates: list[tuple[str, str | None, str | None, str | None]],
                        group: str | None = None, building_fn=None) -> list[Session]:
    """Turn concrete occurrences [(iso_date, start, end, room)] into Sessions.

    A (weekday, start, end, room) pattern that repeats >=3 times over >=3 weeks
    becomes a weekly (or biweekly) series; consecutive-day runs become blocks;
    everything else stays a single date. Works for any source that lists dates.
    """
    from collections import defaultdict
    from datetime import date as _d

    pat = defaultdict(list)
    for iso, s, e, room in dates:
        if not iso:
            continue
        pat[(_d.fromisoformat(iso).weekday(), s, e, room)].append(iso)
    out: list[Session] = []
    leftovers = []
    for (wd, s, e, room), ds in pat.items():
        ds = sorted(set(ds))
        span = (_d.fromisoformat(ds[-1]) - _d.fromisoformat(ds[0])).days
        if len(ds) >= 3 and span >= 14:
            gaps = [(_d.fromisoformat(b) - _d.fromisoformat(a)).days for a, b in zip(ds, ds[1:])]
            biweekly = min(gaps) >= 14
            out.append(Session(weekday=wd, start=s, end=e, rhythm=BIWEEKLY if biweekly else WEEKLY,
                               first_date=ds[0], last_date=ds[-1], room=room,
                               building=building_fn(room) if building_fn and room else None, group=group))
        else:
            leftovers += [(x, s, e, room) for x in ds]
    # merge consecutive days with identical times/room into blocks
    leftovers.sort(key=lambda x: tuple(v or "" for v in x))
    i = 0
    while i < len(leftovers):
        iso, s, e, room = leftovers[i]
        j = i
        while (j + 1 < len(leftovers) and leftovers[j + 1][1:] == (s, e, room)
               and (_d.fromisoformat(leftovers[j + 1][0]) - _d.fromisoformat(leftovers[j][0])).days == 1):
            j += 1
        first, last = iso, leftovers[j][0]
        out.append(Session(weekday=None if j > i else _d.fromisoformat(iso).weekday(), start=s, end=e,
                           rhythm=BLOCK if j > i else SINGLE, first_date=first, last_date=last, room=room,
                           building=building_fn(room) if building_fn and room else None, group=group))
        i = j + 1
    return out
