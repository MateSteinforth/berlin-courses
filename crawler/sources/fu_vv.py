"""FU Berlin – Vorlesungsverzeichnis www.fu-berlin.de/vv (public, plain HTML).

fb (all subjects) -> fach?id= (study programmes) -> modul?id= (course lists)
-> lv/<id> (course page with every single date, room, lecturers, commentary).
Rooms carry street addresses ('1122 Seminarraum (Boltzmannstr. 3)'), so they
are geocoded directly as 'ADDR:<street>' building keys.
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..model import Course, sessions_from_dates
from ..util import clean, get, parse_de_date, session
from . import CatalogSource

log = logging.getLogger(__name__)
BASE = "https://www.fu-berlin.de/vv/de"


def _semester_map(s) -> dict[str, str]:
    """'2026W' -> FU semester id (the 'sm' parameter)."""
    soup = BeautifulSoup(get(s, f"{BASE}/fb").text, "lxml")
    out = {}
    for a in soup.select("a[href*='sm=']"):
        label = clean(a.get_text()) or ""
        m = re.search(r"(WiSe|SoSe|Wintersemester|Sommersemester)\s*(\d{2,4})", label)
        sm = re.search(r"sm=(\d+)", a["href"])
        if m and sm:
            y = int(m.group(2)); y += 2000 if y < 100 else 0
            out.setdefault(f"{y}{'W' if m.group(1).startswith('W') else 'S'}", sm.group(1))
    # the current semester is the default on /fb, recognisable from the page title
    cur = soup.select_one("a.btn.vv_tooltip[title*='ausgewählt']")
    if cur:
        m = re.search(r"(WiSe|SoSe)\s*(\d{2})", cur.get_text())
        sm = re.search(r"sm=(\d+)", str(soup.select_one("a[href*='/fach?id=']")))
        if m and sm:
            out[f"20{m.group(2)}{'W' if m.group(1) == 'WiSe' else 'S'}"] = sm.group(1)
    return out


def fu_building(room: str | None) -> str | None:
    m = re.search(r"\(([^()]*\d[^()]*)\)\s*$", room or "")
    if not m:
        return None
    addr = re.sub(r"\s+", " ", m.group(1)).strip()
    addr = re.sub(r"(str)\.?(\s)", r"straße\2", addr, flags=re.I)  # Boltzmannstr. 3 -> Boltzmannstraße 3
    return f"ADDR:{addr}"


class FuVv(CatalogSource):
    name = "fu_vv"
    university = "FU"

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        s = session()
        sems = _semester_map(s)
        sm = sems.get(semester)
        if not sm:
            log.info("FU: semester %s not available (%s)", semester, sems)
            return []
        fb = BeautifulSoup(get(s, f"{BASE}/fb?sm={sm}").text, "lxml")
        fach_ids = sorted({re.search(r"id=(\d+)", a["href"]).group(1)
                           for a in fb.select("a[href*='/vv/de/fach?id=']")})
        log.info("FU %s (sm=%s): %d subjects", semester, sm, len(fach_ids))

        modules: dict[str, list[str]] = {}
        def fach(fid):
            soup = BeautifulSoup(get(s, f"{BASE}/fach?id={fid}&sm={sm}").text, "lxml")
            name = clean(soup.select_one("h1:not(:first-of-type)").get_text()) if soup.select_one("h1:not(:first-of-type)") else fid
            return [(re.search(r"id=(\d+)", a["href"]).group(1), [name, clean(a.get_text()) or ""])
                    for a in soup.select("a[href*='/vv/de/modul?id=']")]
        with ThreadPoolExecutor(6) as ex:
            for lst in ex.map(fach, fach_ids):
                for mid, path in lst:
                    modules.setdefault(mid, path)
        log.info("FU: %d modules", len(modules))

        lvs: dict[str, list[str]] = {}
        def modul(mid):
            soup = BeautifulSoup(get(s, f"{BASE}/modul?id={mid}&sm={sm}").text, "lxml")
            return mid, {m.group(1) for a in soup.select("a[href*='/vv/de/lv/']")
                         if (m := re.search(r"/lv/(\d+)", a["href"]))}
        with ThreadPoolExecutor(6) as ex:
            for mid, ids in ex.map(modul, list(modules)):
                for lid in ids:
                    lvs.setdefault(lid, modules[mid])
        ids = sorted(lvs, key=int)[: limit or None]
        log.info("FU: %d courses, fetching details", len(ids))

        done = [0]
        lock = threading.Lock()
        def lv(lid):
            try:
                c = self._course(s, lid, sm, semester, lvs[lid])
            except Exception as e:
                log.warning("FU lv %s failed: %s", lid, e)
                return None
            with lock:
                done[0] += 1
                if done[0] % 500 == 0:
                    log.info("FU: %d/%d", done[0], len(ids))
            return c
        with ThreadPoolExecutor(6) as ex:
            return [c for c in ex.map(lv, ids) if c]

    def _course(self, s, lid, sm, semester, path) -> Course | None:
        url = f"{BASE}/lv/{lid}?sm={sm}"
        soup = BeautifulSoup(get(s, url).text, "lxml")
        well = soup.select_one("div.well.course")
        if not well:
            return None
        title = clean(well.find("h1").get_text())
        number = clean(well.select_one(".label_container b").get_text()) if well.select_one(".label_container b") else None
        ctype = clean(well.select_one(".label_container .category").get_text()) if well.select_one(".label_container .category") else None
        lecturers = [x.strip() for x in re.split(r",\s*", clean(well.select_one("h2.instructor_name").get_text()) or "")
                     if x.strip()] if well.select_one("h2.instructor_name") else []

        desc_parts = []
        for h in well.select("div.courses > h3"):
            head = clean(h.get_text())
            box = h.find_next_sibling("div")
            if not box or head in ("Termine",) or re.match(r"\d+ Termine", head or ""):
                continue
            opened = box.select_one(".opened_info") or box
            txt = clean(opened.get_text(" "))
            if txt and head not in ("Anmeldung zu der Veranstaltung",):
                desc_parts.append(f"{head}: {txt}")
        sidebar = soup.select_one(".course_info_sidebar")
        lang = None
        if sidebar:
            m = re.search(r"Unterrichtssprache:\s*(\w+)", sidebar.get_text(" "))
            lang = m.group(1) if m else None

        dates = []
        for b in soup.select("b.course_date_time"):
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", b.get_text())
            if not m:
                continue
            box = b.find_parent(class_="link_to_details")
            room = None
            if box:
                rt = clean(box.get_text(" ")) or ""
                rm = re.search(r"Räume:\s*(.+?)(?:\s+Dozenten:|$)", rt)
                room = rm.group(1).strip() if rm else None
            dates.append((parse_de_date(m.group(1)), m.group(2).zfill(5), m.group(3).zfill(5), room))
        sessions = sessions_from_dates(dates, building_fn=fu_building)
        return Course(
            id=f"{self.name}:{lid}:{semester}", source=self.name, university=self.university,
            semester=semester, title=title, url=f"https://www.fu-berlin.de/vv/de/lv/{lid}?sm={sm}",
            type=ctype, number=number, lecturers=lecturers, org=path[0] if path else None,
            language=lang, description="\n\n".join(desc_parts) or None, sessions=sessions,
            by_arrangement=not sessions, catalog_path=path,
        )
