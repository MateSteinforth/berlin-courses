"""Generic HIS-LSF / QIS catalog ("rds?state=wtree") – used by HU (AGNES) and HTW.

The tree is plain GET: each node is root120262=<id>|<id>|…; leaves link to
course pages (publishConfFile=webInfo) whose tables hold Grunddaten, all
appointment groups (Tag/Zeit/Rhythmus/Dauer/Raum/Gebäude) and a Kommentar.
The catalog shows the university's current default semester; other semesters
need a session switch, which isn't implemented yet (crawl() returns [] then).

To add another LSF university, subclass LsfSource with base URL + root id.
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ..model import BIWEEKLY, BLOCK, IRREGULAR, SINGLE, WEEKLY, Course, Session
from ..util import WEEKDAYS_DE, clean, get, parse_de_date, session
from . import CatalogSource

log = logging.getLogger(__name__)


def _sem_key(label: str | None) -> str | None:
    m = re.search(r"(WiSe|SoSe|WS|SS)\s*(\d{4})", label or "")
    return f"{m.group(2)}{'W' if m.group(1) in ('WiSe', 'WS') else 'S'}" if m else None


class LsfSource(CatalogSource):
    base = ""          # e.g. https://agnes.hu-berlin.de/lupo/rds
    root = ""          # root node id of the lecture index
    workers = 6

    def building(self, gebaeude: str | None, raum: str | None) -> str | None:
        """Default: use the street address in 'Name - Street 1 (ABBR)'."""
        txt = gebaeude or ""
        m = re.search(r"-\s*([^-()]*?\d+[a-zA-Z]?(?:\s*-\s*\d+)?)\s*(?:\(|$)", txt)
        if m and re.search(r"[a-zäöüß]{3,}", m.group(1)):
            return f"ADDR:{m.group(1).strip()}"
        return None

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        s = session()
        start = f"{self.base}?state=wtree&search=1&trex=step&root120262={self.root}&P.vx=kurz"
        seen_nodes = {self.root}
        queue = [(self.root, [])]
        courses: dict[str, list[str]] = {}
        lock = threading.Lock()

        def visit(item):
            node, path = item
            url = f"{self.base}?state=wtree&search=1&trex=step&root120262={node.replace('|', '%7C')}&P.vx=kurz"
            soup = BeautifulSoup(get(s, url).text, "lxml")
            subs, found = [], {}
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if "publishConfFile=webInfo&" in h + "&" and "publishSubDir=veranstaltung" in h:
                    pid = parse_qs(urlparse(h).query).get("publishid", [None])[0]
                    if pid:
                        found[pid] = path
                elif "state=wtree" in h and "root120262=" in h and "P.vx=kurz" in h:
                    r = parse_qs(urlparse(h).query).get("root120262", [""])[0]
                    if r.startswith(node + "|") and r.count("|") == node.count("|") + 1:
                        subs.append((r, path + [clean(a.get_text()) or ""]))
            return subs, found

        _ = start
        while queue:
            batch, queue = queue[: self.workers * 2], queue[self.workers * 2:]
            with ThreadPoolExecutor(self.workers) as ex:
                for subs, found in ex.map(visit, batch):
                    for pid, path in found.items():
                        courses.setdefault(pid, path)
                    for r, p in subs:
                        if r not in seen_nodes:
                            seen_nodes.add(r)
                            queue.append((r, p))
            if limit and len(courses) >= limit:
                break
        n_tree = len(courses)
        # The tree misses courses that aren't filed under it (ASH: 2/3 of all courses!).
        # The search listing has everything; admin entries are filtered out in build.
        if not limit:
            for pid in self._search_ids(s, semester):
                courses.setdefault(pid, [])
        ids = sorted(courses, key=int)[: limit or None]
        log.info("%s: %d tree nodes, %d courses in tree, %d in total with search", self.name,
                 len(seen_nodes), n_tree, len(ids))

        out = []
        done = [0]
        def one(pid):
            try:
                c = self._course(s, pid, semester, courses[pid])
            except Exception as e:
                log.warning("%s %s failed: %s", self.name, pid, e)
                return None
            with lock:
                done[0] += 1
                if done[0] % 1000 == 0:
                    log.info("%s: %d/%d", self.name, done[0], len(ids))
            return c
        with ThreadPoolExecutor(self.workers) as ex:
            out = [c for c in ex.map(one, ids) if c]
        if out and all(c.semester != semester for c in out):
            log.info("%s: catalog shows another semester, not %s", self.name, semester)
            return []
        return [c for c in out if c.semester == semester]

    def _search_ids(self, s, semester: str) -> list[str]:
        """All course ids of a semester from the search listing (100 per page)."""
        code = f"{semester[:4]}{2 if semester.endswith('W') else 1}"   # LSF: 20262 = WiSe 2026/27

        def page(start):
            url = (f"{self.base}?state=wsearchv&search=1&subdir=veranstaltung&veranstaltung.semester={code}"
                   f"&P_start={start}&P_anzahl=100&_form=display")
            soup = BeautifulSoup(get(s, url).text, "lxml")
            return {parse_qs(urlparse(a["href"]).query)["publishid"][0] for a in soup.find_all("a", href=True)
                    if "publishid=" in a["href"] and "publishSubDir=veranstaltung" in a["href"]}

        first = page(0)
        m = re.search(r"(\d+)\s*Treffer", clean(BeautifulSoup(get(s, (
            f"{self.base}?state=wsearchv&search=1&subdir=veranstaltung&veranstaltung.semester={code}"
            f"&P_start=0&P_anzahl=100&_form=display")).text, "lxml").get_text(" ")) or "")
        total = int(m.group(1)) if m else 0
        ids = set(first)
        with ThreadPoolExecutor(self.workers) as ex:
            for r in ex.map(page, range(100, total, 100)):
                ids |= r
        log.info("%s: search lists %d of %d courses", self.name, len(ids), total)
        return sorted(ids)

    def _course(self, s, pid, semester, path) -> Course | None:
        url = (f"{self.base}?state=verpublish&status=init&vmfile=no&publishid={pid}"
               f"&moduleCall=webInfo&publishConfFile=webInfo&publishSubDir=veranstaltung")
        soup = BeautifulSoup(get(s, url).text, "lxml")
        title_el = soup.select_one("h1")
        title = clean(title_el.get_text()) if title_el else None
        if title:
            title = re.sub(r"\s*-\s*(Einzelansicht|Detailseite)$", "", title)
        grund = {}
        for t in soup.find_all("table", summary=re.compile("Grunddaten")):
            for tr in t.find_all("tr"):
                cells = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
                for k, v in zip(cells[::2], cells[1::2]):
                    if k:
                        grund[k] = v
        sem_key = _sem_key(grund.get("Semester")) or semester

        sessions: list[Session] = []
        lecturers: list[str] = []
        for t in soup.find_all("table", summary=re.compile("Veranstaltungstermine")):
            cap = t.find("caption")
            group = clean(cap.get_text()) if cap else None
            heads = [clean(th.get_text(" ")) or "" for th in t.find_all("th")]
            for tr in t.find_all("tr"):
                tds = tr.find_all("td")
                if not tds:
                    continue
                row = dict(zip(heads, [clean(td.get_text(" ")) for td in tds]))
                lec = row.get("Lehrperson")
                if lec:
                    for n in re.split(r";\s*|,\s*(?=[A-ZÄÖÜ][a-zäöü]+,)", lec):
                        if n and n not in lecturers:
                            lecturers.append(n.strip())
                sessions.append(self._session(row, group))
        lecturers = lecturers or [clean(a.get_text()) for a in soup.select("table[summary*='verantwortlichen'] a") if clean(a.get_text())]

        desc = []
        for t in soup.find_all("table", summary=re.compile("Weitere Angaben|Inhalt")):
            for tr in t.find_all("tr"):
                th, td = tr.find("th"), tr.find("td")
                if th and td and clean(td.get_text(" ")):
                    desc.append(f"{clean(th.get_text())}: {clean(td.get_text(' '))}")
        orgs = [clean(a.get_text()) for a in soup.select("table[summary*='Einrichtungen'] a")]
        lms = grund.get("Moodle-Link")
        return Course(
            id=f"{self.name}:{pid}:{sem_key}", source=self.name, university=self.university,
            semester=sem_key, title=title or f"{self.university} {pid}", url=url,
            type=grund.get("Veranstaltungsart"), number=grund.get("Veranstaltungsnummer"),
            lecturers=lecturers, org=(orgs[0] if orgs else (path[0] if path else None)),
            language=grund.get("Sprache"), description="\n\n".join(desc) or None,
            sessions=[x for x in sessions if x], by_arrangement=not any(sessions),
            catalog_path=path, extra={"lms_url": lms} if lms and lms.startswith("http") else {},
        )

    def _session(self, row: dict, group: str | None) -> Session | None:
        tag = (row.get("Tag") or "").lower()[:2]
        zeit = row.get("Zeit") or ""
        tm = re.search(r"(\d{1,2}:\d{2})\s*(?:bis|-)\s*(\d{1,2}:\d{2})", zeit)
        rh = (row.get("Rhythmus") or "").lower()
        dauer = row.get("Dauer") or ""
        ds = re.findall(r"\d{1,2}\.\d{1,2}\.\d{4}", dauer)
        first, last = (parse_de_date(ds[0]), parse_de_date(ds[-1])) if ds else (None, None)
        if rh.startswith("wöch"):
            rhythm = WEEKLY
        elif "14" in rh:
            rhythm = BIWEEKLY
        elif rh.startswith("einzel"):
            rhythm = SINGLE
        elif rh.startswith("block"):
            rhythm = BLOCK
        else:
            rhythm = IRREGULAR
        if not tm and not tag and not ds:
            return None
        room = " ".join(x for x in (row.get("Raum"), row.get("Gebäude")) if x) or None
        return Session(
            weekday=WEEKDAYS_DE.get(tag), start=tm.group(1).zfill(5) if tm else None,
            end=tm.group(2).zfill(5) if tm else None, rhythm=rhythm, first_date=first, last_date=last,
            room=room, building=self.building(row.get("Gebäude"), row.get("Raum")), group=group,
            note=row.get("Bemerkung"),
        )


class HuAgnes(LsfSource):
    name = "hu_agnes"
    university = "HU"
    base = "https://agnes.hu-berlin.de/lupo/rds"
    root = "342437"   # "Humboldt-Universität zu Berlin" lecture index WiSe 2026/27


class HtwLsf(LsfSource):
    name = "htw_lsf"
    university = "HTW"
    base = "https://lsf.htw-berlin.de/qisserver/rds"
    root = "37456"

    def building(self, gebaeude, raum):
        # HTW rooms look like 'WH C 625' (Wilhelminenhof) / 'TA A 101' (Treskowallee)
        txt = f"{gebaeude or ''} {raum or ''}"
        if re.search(r"\bWH\b|Wilhelminenhof", txt):
            return "ADDR:Wilhelminenhofstraße 75A"
        if re.search(r"\bTA\b|Treskowallee", txt):
            return "ADDR:Treskowallee 8"
        return super().building(gebaeude, raum)


class AshLsf(LsfSource):
    name = "ash_lsf"
    university = "ASH"
    base = "https://www.ash-berlin.eu/qisserver/rds"
    root = "8509|8513"   # "Vorlesungsverzeichnis" (skips committees, staff events)

    def building(self, gebaeude, raum):
        return "ADDR:Alice-Salomon-Platz 5"  # single campus in Hellersdorf
