"""Moodle course spaces (UdK Moodle, TU ISIS) – guest-browsable category tree.

Moodle doesn't carry schedules, so these are used to
  * link catalog courses to their Moodle/ISIS course space (by title), and
  * add a description where the catalog has none.

Only courses whose name mentions one of the wanted semesters (or no semester
at all) are kept; ISIS has tens of thousands of archived courses.

Login: guest browsing is enough today. If a site starts requiring login, set
<NAME>_USER / <NAME>_PASS (e.g. ISIS_USER) and implement `login()`.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..model import LmsCourse
from ..util import clean, get, semester_keys, session
from . import LmsSource

log = logging.getLogger(__name__)

_SEM = re.compile(
    r"\b(WiSe|WS|Wintersemester|Winter|SoSe|SS|Sommersemester|Summer|Sommer)\s*[-_.]?\s*"
    r"(20)?(\d{2})(?:\s*/\s*(20)?(\d{2}))?", re.I)


def semester_of(name: str) -> str | None:
    """'WiSe 2026/27' / 'WS 26/27' / 'SoSe25' -> '2026W' / '2025S'."""
    m = _SEM.search(name)
    if not m:
        return None
    kind = m.group(1).lower()
    year = 2000 + int(m.group(3))
    return f"{year}{'W' if kind.startswith('w') else 'S'}"


class MoodleSource(LmsSource):
    base = ""
    workers = 8

    def crawl(self, limit: int | None = None) -> list[LmsCourse]:
        wanted = set(semester_keys())
        s = session()
        seen: dict[str, list[str]] = {}
        queue: list[tuple[str | None, list[str]]] = [(None, [])]
        out: dict[str, LmsCourse] = {}
        t0 = time.time()

        def fetch(item):
            cid, path = item
            url = f"{self.base}/course/index.php" + (f"?categoryid={cid}&perpage=all" if cid else "")
            return cid, path, BeautifulSoup(get(s, url).text, "lxml")

        while queue:
            batch, queue = queue[:self.workers], queue[self.workers:]
            with ThreadPoolExecutor(self.workers) as ex:
                for cid, path, soup in ex.map(fetch, batch):
                    for a in soup.select(".categoryname a, .subcategories h3 a, .category h3 a"):
                        m = re.search(r"categoryid=(\d+)", a.get("href", ""))
                        if m and m.group(1) not in seen:
                            name = clean(a.get_text()) or ""
                            sub = path + [name]
                            seen[m.group(1)] = sub
                            # skip archived semester categories entirely
                            sem = semester_of(name)
                            if sem and sem not in wanted:
                                continue
                            queue.append((m.group(1), sub))
                    for box in soup.select(".coursebox"):
                        c = self._course(box, path)
                        if c:
                            out[c.id] = c
            if limit and len(out) >= limit:
                break
        log.info("%s: %d categories, %d current courses in %.0fs",
                 self.name, len(seen), len(out), time.time() - t0)
        return list(out.values())

    def _course(self, box, path: list[str]) -> LmsCourse | None:
        a = box.select_one(".coursename a")
        if not a:
            return None
        name = clean(a.get_text()) or ""
        sem = semester_of(name) or next((semester_of(p) for p in reversed(path) if semester_of(p)), None)
        wanted = set(semester_keys())
        if sem and sem not in wanted:
            return None
        summ = box.select_one(".summary")
        teachers = [clean(li.get_text(" ")) for li in box.select(".teachers li")]
        teachers = [re.sub(r"^[^:]{0,30}:\s*", "", t) for t in teachers if t]
        cid = box.get("data-courseid") or re.search(r"id=(\d+)", a["href"]).group(1)
        return LmsCourse(
            id=f"{self.name}:{cid}",
            source=self.name,
            university=self.university,
            title=name,
            url=a["href"],
            category=path,
            summary=clean(summ.get_text(" ")) if summ else None,
            teachers=teachers,
            semester_hint=sem,
        )


class UdkMoodle(MoodleSource):
    name = "udk_moodle"
    university = "UdK"
    base = "https://moodle.udk-berlin.de/moodle"


class TuIsis(MoodleSource):
    name = "tu_isis"
    university = "TU"
    base = "https://isis.tu-berlin.de"
    workers = 12
