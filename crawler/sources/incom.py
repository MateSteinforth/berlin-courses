"""Incom course catalogs (design schools): KHB (see.incom.org), FH Potsdam (fhp.incom.org).

<host>/vlv/<year>-<1|2> lists every course of a semester (1 = summer,
2 = winter) with field, module, lecturers, format, language, first date and
room. The linked workspace 'about' page has the full description and a 'Wann'
line. Both schools have one campus, so rooms map to that address.
Dates for block formats are usually only in the description text; Claude
Haiku extracts them (same as UdK Studium Generale).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..model import WEEKLY, Course, Session
from ..util import WEEKDAYS_DE, clean, get, parse_de_date, semester_label, session
from . import CatalogSource

log = logging.getLogger(__name__)
_WD = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3, "freitag": 4, "samstag": 5, "sonntag": 6}


class IncomSource(CatalogSource):
    base = ""
    campus = ""   # ADDR: building key for the single campus

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        s = session()
        code = f"{semester[:4]}-{'2' if semester.endswith('W') else '1'}"
        soup = BeautifulSoup(get(s, f"{self.base}/vlv/{code}").text, "lxml")
        sel = soup.select_one("#semester-selectbox option[selected]")
        label = ("Wintersemester " if semester.endswith("W") else "Sommersemester ") + semester[:4]
        if (sel and sel.get("value") != code) or (not sel and label not in soup.get_text(" ")):
            log.info("%s: semester %s not on Incom yet", self.name, semester)
            return []
        items = soup.select("div.c-vlv__item")[: limit or None]
        log.info("%s %s: %d courses", self.name, semester, len(items))

        def one(it):
            try:
                return self._course(s, it, semester)
            except Exception as e:
                log.warning("Incom item failed: %s", e)
                return None
        with ThreadPoolExecutor(4) as ex:
            courses = [c for c in ex.map(one, items) if c]

        # courses without a weekly 'Wann' get their dates extracted from the text
        need = [c for c in courses if not c.sessions]
        if need:
            from ..enrich import extract_sessions
            got = extract_sessions([(c.id, c.extra["raw_dates"], semester_label(semester)) for c in need])
            for c in need:
                c.sessions = [Session(**x) for x in got.get(c.id, [])]
                for x in c.sessions:
                    x.building = self.campus
                    x.room = x.room or c.extra.get("room")
        for c in courses:
            c.by_arrangement = not c.sessions
        return courses

    def _course(self, s, it, semester) -> Course | None:
        for icon in it.select("a.c-vlv-row i"):
            icon.decompose()  # 'Ausklappen'/'Einklappen' toggle labels
        spans = it.select("a.c-vlv-row > span.c-vlv-row__field")
        # anchor on the bold title column; some schools omit empty columns (e.g. module)
        ti = next((i for i, x in enumerate(spans) if x.find("b")), 2)
        f = [clean(x.get_text(" ")) for x in spans]
        before, after = f[:ti], f[ti + 1:]
        field = before[0] if before else None
        module = before[1] if len(before) > 1 else None
        title = f[ti] if ti < len(f) else None
        lecturers, wann_list, lang = (after + [None] * 3)[:3]
        fmt = next((k for k in ("Blockseminar", "Kompaktseminar", "Workshop", "Exkursion", "Vorlesung",
                                "Seminar", "Kolloquium", "Entwurfsprojekt", "Projekt", "Kurs")
                    if k.lower() in f"{wann_list} {module} {title}".lower()), None)
        details = {clean(dt.get_text()): clean(dt.find_next("dd").get_text(" ")) for dt in it.select("dl dt")}
        link = it.select_one("a[href*='/workspace/']")
        url = link["href"] if link else f"{self.base}/vlv"
        wid = re.search(r"/workspace/(\d+)", url).group(1) if link else re.sub(r"\W+", "-", title or "")[:40]

        desc, wann = None, None
        if link:
            about = BeautifulSoup(get(s, url).text, "lxml")
            main = about.select_one("main") or about.body
            for x in main(["script", "style", "nav", "header", "footer"]):
                x.decompose()
            info = main.find(string=re.compile(r"^\s*Info\s*$"))
            txt = clean(main.get_text(" ")) or ""
            m = re.search(r"\bInfo\b\s*(.*?)\s*(Fachgruppe|Semester\s+(Winter|Sommer))", txt)
            desc = m.group(1) if m else None
            w = re.search(r"Wann\s+(.*?)\s+(Erster Termin|Prüfungsleistung|Ort|Credits|$)", txt)
            wann = w.group(1) if w else None
            _ = info
        wann = wann or wann_list
        if not desc:
            d = it.select_one(".c-vlv-details__center p")
            desc = clean(d.get_text(" ")) if d else None

        first = parse_de_date(details.get("Erster Termin") or "")
        room = details.get("Ort")
        sessions = []
        if wann:
            m = re.match(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)s?,?\s*"
                         r"(\d{1,2})[:.](\d{2})\s*[–-]?\s*(?:(\d{1,2})[:.](\d{2}))?", wann)
            if m:
                sessions.append(Session(
                    weekday=_WD[m.group(1).lower()], start=f"{int(m.group(2)):02d}:{m.group(3)}",
                    end=f"{int(m.group(4)):02d}:{m.group(5)}" if m.group(4) else None,
                    rhythm=WEEKLY, first_date=first, room=room, building=self.campus))
        credits = it.select_one(".c-vlv-details__left-module i")
        return Course(
            id=f"{self.name}:{wid}:{semester}", source=self.name, university=self.university,
            semester=semester, title=title or "?", url=url, type=fmt,
            lecturers=[x.strip() for x in re.split(r",\s*", lecturers or "") if x.strip()],
            org=field, language={"DE": "Deutsch", "EN": "Englisch"}.get(lang or "", lang),
            description=desc, sessions=sessions, catalog_path=[x for x in (field, module) if x],
            extra={"raw_dates": f"Format: {fmt}. Wann: {wann}. Erster Termin: {details.get('Erster Termin')}. "
                                f"Ort: {room}.\n{(desc or '')[:900]}",
                   "room": room, "credits": clean(credits.get_text()) if credits else None},
        )



class KhbIncom(IncomSource):
    name = "khb_incom"
    university = "KHB"
    base = "https://see.incom.org"
    campus = "ADDR:Bühringstraße 20"


class FhpIncom(IncomSource):
    name = "fhp_incom"
    university = "FHP"
    base = "https://fhp.incom.org"
    campus = "ADDR:Kiepenheuerallee 5, 14469 Potsdam"


_ = WEEKDAYS_DE
