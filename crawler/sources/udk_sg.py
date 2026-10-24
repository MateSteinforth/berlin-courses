"""UdK Studium Generale – www.udk-berlin.de/studium/studium-generale/kurse-courses/

These pages list the Studium Generale seminars, workshops, block seminars and
"Kompaktwochen" with free-text dates ('Sa/So 7./8.11. & 14./15.11.2026,
11–17 h, HZT Uferstudios'). We collect every course page of the semester and
let Claude Haiku turn the free text into structured sessions (cached by text
hash; without an API key the course is kept with its description but no dates).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..model import Course, Session
from ..util import clean, get, semester_label, session
from . import CatalogSource

log = logging.getLogger(__name__)
INDEX = "https://www.udk-berlin.de/studium/studium-generale/kurse-courses/"


def _slug(semester: str) -> str:
    y = int(semester[:4])
    return f"wise-{y}-{str(y + 1)[2:]}" if semester.endswith("W") else f"sose-{y}"


class UdkStudiumGenerale(CatalogSource):
    name = "udk_sg"
    university = "UdK"

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        s = session()
        slug = _slug(semester)
        idx = BeautifulSoup(get(s, INDEX).text, "lxml")
        lists = sorted({urljoin(INDEX, a["href"]) for a in idx.select("main a[href]")
                        if slug in a["href"] and a["href"].startswith("/studium/studium-generale/")})
        if not lists:
            log.info("UdK SG: no pages for %s yet", semester)
            return []
        items: dict[str, str] = {}
        for lst in lists:
            soup = BeautifulSoup(get(s, lst).text, "lxml")
            section = clean(soup.select_one("main h1").get_text()) if soup.select_one("main h1") else lst
            for a in soup.select("main a[href]"):
                u = urljoin(lst, a["href"])
                if u.startswith(lst) and u != lst:
                    items.setdefault(u, section)
                # 'opened courses' pages link to sub-lists one level deeper
            for a in soup.select("main a[href]"):
                u = urljoin(lst, a["href"])
                if slug in u and u not in lists and u not in items and u.startswith(INDEX) and u.count("/") > lst.count("/"):
                    items.setdefault(u, section)
        urls = sorted(items)[: limit or None]
        log.info("UdK SG %s: %d course pages", semester, len(urls))

        def one(u):
            try:
                return self._course(s, u, items[u], semester)
            except Exception as e:
                log.warning("UdK SG %s failed: %s", u, e)
                return None
        with ThreadPoolExecutor(4) as ex:
            courses = [c for c in ex.map(one, urls) if c]

        from ..enrich import extract_sessions
        extracted = extract_sessions([(c.id, c.extra["raw_dates"], semester_label(semester)) for c in courses])
        for c in courses:
            ss = extracted.get(c.id) or []
            c.sessions = [Session(**x) for x in ss]
            c.by_arrangement = not c.sessions
        return courses

    def _course(self, s, url, section, semester) -> Course | None:
        soup = BeautifulSoup(get(s, url).text, "lxml")
        main = soup.select_one("main")
        if not main:
            return None
        for x in main(["script", "style", "nav"]):
            x.decompose()
        h1 = main.find(["h1"])
        title = clean(h1.get_text()) if h1 else None
        text = clean(main.get_text(" ")) or ""
        text = re.sub(r"(Weiterempfehlen\s*)?Seite (per E-Mail|auf Facebook) weiterempfehlen\s*", "", text)
        if not title or len(text) < 80 or title.startswith("Geöffnete"):
            return None  # 'opened courses' overview pages list HISinOne courses we already have
        meta = re.search(r"(Blockseminar|Seminar|Ringseminar|Kompaktwoche|Intensive Workshop|Workshop|Vorlesung|Ringvorlesung|Projektwoche)[^.]{0,80}?ECTS", text)
        ctype = meta.group(1) if meta else None
        lec = main.select_one(".meta-info")
        # the date/location paragraph is near the top: keep the first ~900 chars for extraction
        return Course(
            id=f"{self.name}:{url.rstrip('/').rsplit('/', 1)[-1]}:{semester}",
            source=self.name, university=self.university, semester=semester,
            title=title, url=url, type=ctype,
            lecturers=[clean(lec.get_text())] if lec and clean(lec.get_text()) else [],
            org="Studium Generale", language=_lang(meta.group(0)) if meta else None,
            description=text[:6000], catalog_path=["Studium Generale", section],
            extra={"raw_dates": text[:900], "studium_generale": True},
        )


def _lang(s: str) -> str | None:
    s = s.lower()
    de, en = "deutsch" in s, ("english" in s or "englisch" in s)
    return "Deutsch/Englisch" if de and en else "Deutsch" if de else "Englisch" if en else None
