"""When does each university's catalog change? -> when do we recrawl?

Researched 2026-10-25. Lecture periods per university and semester, with the
source we got them from. Each source recrawls a semester at:

  * catalog publication  (known date, or estimated as lecture start - 8 weeks;
                          until it's published we re-check weekly)
  * lecture start - 14d  (rooms/times final for most courses)
  * lecture start + 7d   (first-week changes, late additions)
  * lecture start + 35d  (block seminars and late entries are added during the term)

Semesters not listed below fall back to the typical pattern for the
university type (see _default_period), so the schedule keeps working; update
this file once a year when the universities publish their calendars.
"""
from __future__ import annotations

from datetime import date, timedelta

# (lecture start, lecture end, source)
PERIODS: dict[str, dict[str, tuple[str, str, str]]] = {
    # FU publishes the joint calendar; TU/HU/UdK follow the same Berlin university dates
    # (TU's MOSES page is consistent: courses for SoSe 27 can be added from 15.02.2027).
    "FU": {
        "2026W": ("2026-10-12", "2027-02-13", "https://www.fu-berlin.de/studium/beratung/kalender/2026wise/index.html"),
        "2027S": ("2027-04-12", "2027-07-17", "https://www.fu-berlin.de/studium/beratung/kalender/2027sose/index.html"),
    },
    "TU": {"2026W": ("2026-10-12", "2027-02-13", "assumed = FU"), "2027S": ("2027-04-12", "2027-07-17", "assumed = FU")},
    "HU": {"2026W": ("2026-10-12", "2027-02-13", "assumed = FU"), "2027S": ("2027-04-12", "2027-07-17", "assumed = FU")},
    "UdK": {"2026W": ("2026-10-12", "2027-02-13", "assumed = FU"), "2027S": ("2027-04-12", "2027-07-17", "assumed = FU")},
    "KHB": {"2026W": ("2026-10-12", "2027-02-12", "https://kh-berlin.de/studium/studienorganisation/hochschuljahr")},
    "HTW": {
        "2026W": ("2026-10-01", "2027-02-13", "https://www.htw-berlin.de/fileadmin/HTW/Zentral/Rechtsstelle/Amtliche_Mitteilungsblaetter/2024/11_24.pdf"),
        "2027S": ("2027-04-01", "2027-07-24", "https://www.htw-berlin.de/fileadmin/HTW/Zentral/Rechtsstelle/Amtliche_Mitteilungsblaetter/2024/11_24.pdf"),
    },
    "ASH": {
        "2026W": ("2026-10-01", "2027-02-13", "https://www.ash-berlin.eu/studium/studierendenverwaltung/semester-vorlesungszeiten/"),
        "2027S": ("2027-04-01", "2027-07-16", "https://www.ash-berlin.eu/studium/studierendenverwaltung/semester-vorlesungszeiten/"),
    },
    "BHT": {"2026W": ("2026-10-01", "2027-02-13", "https://www.bht-berlin.de/termine"),
            "2027S": ("2027-04-01", "2027-07-24", "https://www.bht-berlin.de/termine")},
    "HWR": {"2026W": ("2026-10-01", "2027-02-13", "https://www.hwr-berlin.de/studium/studieren-an-der-hwr-berlin/studienorganisation/fristen-und-termine")},
}

# Known catalog publication dates. TU: 1 March (summer) / 1 August (winter),
# WiSe 26/27 exceptionally 15 September
# (https://www.tu.berlin/innocampus/projekte/moses/support/lehrveranstaltungsplanung-vpl).
PUBLICATION: dict[str, dict[str, str]] = {
    "TU": {"2026W": "2026-09-15", "2027S": "2027-03-01", "2027W": "2027-08-01"},
}
PUBLICATION_RULE = {"TU": ((3, 1), (8, 1))}  # (summer, winter) month/day for later years

APPLIED_SCIENCES = {"HTW", "ASH", "BHT", "HWR", "FHP"}  # semesters start 1 Oct / 1 Apr


def _default_period(uni: str, sem: str) -> tuple[date, date]:
    y = int(sem[:4])
    if uni in APPLIED_SCIENCES:
        return (date(y, 10, 1), date(y + 1, 2, 13)) if sem.endswith("W") else (date(y, 4, 1), date(y, 7, 20))
    # universities/art schools: second Monday of October / April
    first = date(y, 10, 1) if sem.endswith("W") else date(y, 4, 1)
    second_monday = first + timedelta(days=(7 - first.weekday()) % 7 + 7)
    end = date(y + 1, 2, 13) if sem.endswith("W") else date(y, 7, 17)
    return second_monday, end


def lecture_period(uni: str, sem: str) -> tuple[date, date]:
    p = PERIODS.get(uni, {}).get(sem)
    if p:
        return date.fromisoformat(p[0]), date.fromisoformat(p[1])
    return _default_period(uni, sem)


def publication(uni: str, sem: str) -> date:
    known = PUBLICATION.get(uni, {}).get(sem)
    if known:
        return date.fromisoformat(known)
    if uni in PUBLICATION_RULE:
        (sm, sd), (wm, wd) = PUBLICATION_RULE[uni]
        y = int(sem[:4])
        return date(y, wm, wd) if sem.endswith("W") else date(y, sm, sd)
    return lecture_period(uni, sem)[0] - timedelta(weeks=8)


def refresh_dates(uni: str, sem: str) -> list[date]:
    start, _ = lecture_period(uni, sem)
    return sorted({publication(uni, sem), start - timedelta(days=14),
                   start + timedelta(days=7), start + timedelta(days=35)})


def next_semesters(sem: str, n: int = 3) -> list[str]:
    out, y, k = [], int(sem[:4]), sem[4]
    for _ in range(n):
        if k == "W":
            y, k = y + 1, "S"
        else:
            k = "W"
        out.append(f"{y}{k}")
    return out


def next_update(uni: str, sem: str, after: date, published: bool) -> date:
    """Next date to crawl `sem` of `uni` after `after`.

    Not yet published -> weekly checks from the expected publication date.
    Published -> next scheduled refresh; after the last one, the semester is done
    (returns a date far in the future so the scheduler skips it).
    """
    pub = publication(uni, sem)
    if not published:
        return max(pub, after + timedelta(days=7))
    for d in refresh_dates(uni, sem):
        if d > after:
            return d
    return date(9999, 12, 31)

