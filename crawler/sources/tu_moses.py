"""TU Berlin – MOSES Vorlesungsverzeichnis (public, no login).

1. The org-unit view of the TU root (oe=927, incl. subordinates) lists every
   course of a semester. It's a PrimeFaces table, so we page through it with
   Playwright (100 rows/page) to collect course template ids.
2. Each course page (vorlage.html?veranstaltungsvorlage=ID&semester=N) is
   plain server-rendered HTML; its calendar popovers carry labelled fields
   (Datum/Uhrzeit, Ort, Dozierende, …) which we parse with requests + bs4.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..model import (BIWEEKLY, BLOCK, DAILY, IRREGULAR, SINGLE, WEEKLY, Course,
                     Session)
import threading
import time

from ..util import CACHE, WEEKDAYS_DE, clean, get, load_json, parse_de_date, save_json, session
from . import CatalogSource

log = logging.getLogger(__name__)

BASE = "https://moseskonto.tu-berlin.de/moses/verzeichnis/veranstaltungen"
ROOT_OE = 927  # "Technische Universität Berlin"
MODULE_LINKS = CACHE / "moses_module_links.json"   # veranstaltung id -> [module url paths]
MODULE_TEXT = CACHE / "moses_modules.json"         # module url path -> description text
WORKERS = 6


def _semester_map(html: str) -> dict[str, tuple[int, bool]]:
    """'2026W' -> (moses semester id, published?) from the semester picker."""
    out = {}
    soup = BeautifulSoup(html, "lxml")
    for inp in soup.select("input[type=radio][name$='semester-picker:j_idt79'], "
                           "div[data-testid=semester-picker-select-one-button] input[type=radio]"):
        div = inp.parent
        label = clean(div.get_text())
        m = re.match(r"(SoSe|WiSe) (\d{4})", label or "")
        if not m:
            continue
        key = f"{m.group(2)}{'S' if m.group(1) == 'SoSe' else 'W'}"
        disabled = "disabled" in (div.get("class") or [])
        out[key] = (int(inp["value"]), not disabled)
    return out


class TuMoses(CatalogSource):
    name = "tu_moses"
    university = "TU"

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        ids, sem_id = self._list_ids(semester, limit)
        if not ids:
            return []
        log.info("MOSES %s: %d course ids, fetching details", semester, len(ids))
        s = session()

        def one(vid: str) -> Course | None:
            try:
                return self._course(s, vid, sem_id, semester)
            except Exception as e:  # keep going, one broken page shouldn't kill the crawl
                log.warning("MOSES %s failed: %s", vid, e)
                return None

        with ThreadPoolExecutor(max_workers=4) as ex:
            courses = [c for c in ex.map(one, ids) if c]
        self._attach_module_descriptions(courses)
        return courses

    # ---- descriptions --------------------------------------------------------
    def _attach_module_descriptions(self, courses: list[Course]) -> None:
        """MOSES courses carry no description of their own; their Bologna module
        descriptions (learning outcomes + contents) do. The module list of a
        course sits in a lazily loaded JSF tab, so we click it with Playwright."""
        from playwright.sync_api import sync_playwright

        links = load_json(MODULE_LINKS, {})
        texts = load_json(MODULE_TEXT, {})
        todo = sorted({c.extra["veranstaltung"] for c in courses
                       if c.extra.get("veranstaltung") and c.extra["veranstaltung"] not in links})
        log.info("MOSES: looking up modules for %d courses (%d cached)", len(todo), len(links))
        lock = threading.Lock()

        def worker(chunk):
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                pg.set_default_timeout(45_000)
                for i, vid in enumerate(chunk):
                    try:
                        pg.goto(f"{BASE}/veranstaltung.html?veranstaltung={vid}")
                        tab = pg.get_by_text("Studiengangszuordnungen und Module")
                        if tab.count() == 0:  # course not assigned to any module
                            with lock:
                                links[vid] = []
                            continue
                        tab.first.click()
                        pg.wait_for_load_state("networkidle")
                        pg.wait_for_timeout(400)
                        found = sorted(set(re.findall(
                            r'(/moses/modultransfersystem/bolognamodule/beschreibung/anzeigen\.html\?number=\d+&(?:amp;)?version=\d+)',
                            pg.content())))
                        with lock:
                            links[vid] = [f.replace("&amp;", "&") for f in found]
                    except Exception as e:
                        log.debug("module tab %s failed: %s", vid, e)
                    if i % 200 == 199:
                        with lock:
                            save_json(MODULE_LINKS, links)
                b.close()

        t0 = time.time()
        with ThreadPoolExecutor(WORKERS) as ex:
            list(ex.map(worker, [todo[k::WORKERS] for k in range(WORKERS) if todo[k::WORKERS]]))
        save_json(MODULE_LINKS, links)
        log.info("MOSES: module links done in %.0fs", time.time() - t0)

        s = session()
        need = sorted({m for c in courses for m in links.get(c.extra.get("veranstaltung", ""), []) if m not in texts})
        log.info("MOSES: fetching %d module descriptions", len(need))

        def fetch(path):
            try:
                return path, _module_text(get(s, "https://moseskonto.tu-berlin.de" + path).text)
            except Exception as e:
                log.warning("module %s failed: %s", path, e)
                return path, None
        with ThreadPoolExecutor(4) as ex:
            for path, txt in ex.map(fetch, need):
                if txt is not None:
                    texts[path] = txt
        save_json(MODULE_TEXT, texts)

        n = 0
        for c in courses:
            mods = [texts[m] for m in links.get(c.extra.get("veranstaltung", ""), []) if texts.get(m)]
            if mods:
                # several study programmes often share one module; keep distinct texts, max 2
                uniq = list(dict.fromkeys(mods))[:2]
                c.description = "\n\n---\n\n".join(uniq)
                n += 1
        log.info("MOSES: %d/%d courses have a module description", n, len(courses))

    # ---- listing ---------------------------------------------------------
    def _list_ids(self, semester: str, limit: int | None) -> tuple[list[str], int | None]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page()
            pg.set_default_timeout(180_000)
            pg.goto(f"{BASE}/organisationseinheit.html")
            sems = _semester_map(pg.content())
            if semester not in sems or not sems[semester][1]:
                log.info("MOSES: semester %s not published (%s)", semester, sems)
                browser.close()
                return [], None
            sem_id = sems[semester][0]
            pg.goto(f"{BASE}/organisationseinheit.html?oe={ROOT_OE}"
                    f"&include-subordinates=true&search=true&semester={sem_id}")
            pg.wait_for_load_state("networkidle")
            # stay at 50 rows: switching to 100 rebuilds the paginator and its "next" button stops working
            ids: list[str] = []
            seen = set()
            empty_pages = 0
            for _ in range(200):
                before = len(ids)
                for vid in re.findall(r"veranstaltungsvorlage=(\d+)", pg.content()):
                    if vid not in seen:
                        seen.add(vid)
                        ids.append(vid)
                empty_pages = empty_pages + 1 if len(ids) == before else 0
                if (limit and len(ids) >= limit) or empty_pages >= 2:
                    break
                nxt = pg.locator(".ui-paginator-next").last
                if not nxt.count() or "ui-state-disabled" in (nxt.get_attribute("class") or ""):
                    break
                cur = pg.locator(".ui-paginator-page.ui-state-active").last.inner_text()
                for attempt in range(3):  # MOSES sometimes drops a paging request under load
                    try:
                        # the numbered link is reliable; fall back to "next" when it isn't in the visible window
                        num = pg.locator(".ui-paginator-page", has_text=re.compile(rf"^{int(cur) + 1}$")).last
                        (num if num.count() else nxt).click()
                        pg.wait_for_function(
                            """(cur) => { const a = [...document.querySelectorAll('.ui-paginator-page.ui-state-active')];
                                          return a.length && a[a.length-1].textContent.trim() !== cur; }""",
                            arg=cur, timeout=60_000)
                        break
                    except Exception:
                        log.warning("MOSES: page after %s did not load (attempt %d)", cur, attempt + 1)
                        pg.wait_for_timeout(5000)
                else:
                    raise RuntimeError(f"MOSES paginator stuck after page {cur} ({len(ids)} ids) – not saving a partial list")
                pg.wait_for_timeout(300)
            browser.close()
        return (ids[:limit] if limit else ids), sem_id

    # ---- detail ----------------------------------------------------------
    def _course(self, s, vid: str, sem_id: int, semester: str) -> Course | None:
        url = f"{BASE}/vorlage.html?veranstaltungsvorlage={vid}&semester={sem_id}"
        soup = BeautifulSoup(get(s, url).text, "lxml")
        pops = soup.select("[data-popover-class*=termin-popover]")
        entries = []
        for pop in pops:
            content = pop.find("div", class_="content")
            title = pop.find("div", class_="title")
            if not content:
                continue
            fields = {}
            for fg in content.select("div.form-group"):
                lab = fg.find("label")
                if not lab:
                    continue
                key = clean(lab.get_text())
                lab.extract()
                fields[key] = clean(fg.get_text(" "))
            entries.append((clean(title.get_text()) if title else None, fields))

        title_text = entries[0][0] if entries else None
        if not title_text:
            hh = soup.select_one("h1, h2, .page-header")
            title_text = clean(hh.get_text()) if hh else f"MOSES {vid}"

        m = re.match(r"(.*)\(([^()]*)\)\s*$", title_text or "")
        title, ctype = (clean(m.group(1)), clean(m.group(2))) if m else (title_text, None)

        sessions: list[Session] = []
        seen = set()
        lecturers: list[str] = []
        langs, orgs, numbers = set(), set(), set()
        for _, f in entries:
            dt, ort, grp = f.get("Datum/Uhrzeit"), f.get("Ort"), f.get("Gruppe")
            if f.get("Dozierende"):
                for name in re.split(r";\s*|\s*/\s*", f["Dozierende"]):
                    if name and name not in lecturers:
                        lecturers.append(name)
            if f.get("Sprache"):
                langs.add(f["Sprache"])
            if f.get("Organisationseinheiten"):
                orgs.add(f["Organisationseinheiten"])
            if f.get("LV-Nummer") and f["LV-Nummer"] != "-":
                numbers.add(f["LV-Nummer"])
            k = (dt, ort, grp)
            if not dt or k in seen:
                continue
            seen.add(k)
            sessions.extend(parse_moses_datetime(dt, ort, grp))

        if not entries and not soup.find(string=re.compile(r"Termingruppe|Gruppe")):
            return None
        org = sorted(orgs)[0] if orgs else None
        vm = soup.find("a", href=re.compile(r"veranstaltung\.html\?veranstaltung=\d+"))
        veranst = re.search(r"veranstaltung=(\d+)", vm["href"]).group(1) if vm else None
        return Course(
            id=f"{self.name}:{vid}:{semester}",
            source=self.name,
            university=self.university,
            semester=semester,
            title=title or f"MOSES {vid}",
            url=url,
            type=ctype,
            number=sorted(numbers)[0] if numbers else None,
            lecturers=lecturers,
            org=re.sub(r"^\d+\s+", "", org) if org else None,
            language=", ".join(sorted(langs)) or None,
            sessions=sessions,
            by_arrangement=not sessions,
            extra={"veranstaltung": veranst} if veranst else {},
        )


_TIME = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_WD = re.compile(r"\b(Mo|Di|Mi|Do|Fr|Sa|So)\.")
_DATE = re.compile(r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?")


def parse_moses_datetime(dt: str, ort: str | None, grp: str | None) -> list[Session]:
    """Parse strings like
    'Di. 13.10 - 15.12.26, wöchentlich, Di. 05.01 - 09.02.27, wöchentlich, 12:00 - 14:00'
    'Di. 13.10 - Fr. 16.10.26, täglich, 08:00 - 16:00, unregelmäßig'
    'Di. 13.10.26, 12:00 - 14:00'
    """
    tm = _TIME.search(dt)
    start, end = (tm.group(1).zfill(5), tm.group(2).zfill(5)) if tm else (None, None)
    low = dt.lower()
    if any(k in low for k in ("14-täglich", "14-tägig", "zweiwöchentlich", "alle zwei wochen", "14tägl")):
        rhythm = BIWEEKLY
    elif "wöchentlich" in low:
        rhythm = WEEKLY
    elif "täglich" in low:
        rhythm = DAILY
    else:
        rhythm = SINGLE
    if "unregelmäßig" in low and rhythm == SINGLE:
        rhythm = IRREGULAR

    first, last = _date_span(dt)
    if rhythm == DAILY:
        rhythm = BLOCK

    wd = _WD.search(dt)
    weekday = WEEKDAYS_DE[wd.group(1).lower()] if wd else None
    weekdays = {WEEKDAYS_DE[w.lower()] for w in _WD.findall(dt)}
    if rhythm == BLOCK and len(weekdays) > 1:
        weekday = None

    rooms = None
    building = None
    if ort and ort != "Ohne Ort":
        rooms = ort
        building = moses_building(ort)
    return [Session(weekday=weekday, start=start, end=end, rhythm=rhythm,
                    first_date=first, last_date=last, room=rooms, building=building,
                    group=grp)]


def moses_building(ort: str) -> str | None:
    """'Wedding, TIB20+21 004' -> 'TU:Wedding/TIB'; 'Charlottenburg, H 0104' -> 'TU:Charlottenburg/H'."""
    m = re.match(r"\s*([^,]+),\s*(.+)", ort)
    if not m:
        return f"TU:{ort.split()[0]}" if ort.strip() else None
    campus, rest = m.group(1).strip(), m.group(2).strip()
    first = rest.split(",")[0].strip()
    code = re.match(r"([A-Za-zÄÖÜäöü][A-Za-zÄÖÜäöü\-]*[A-Za-zÄÖÜäöü]|[A-Za-zÄÖÜäöü])", first)
    return f"TU:{campus}/{code.group(1) if code else first.split()[0]}"


def _date_span(dt: str) -> tuple[str | None, str | None]:
    """Earliest and latest date in a MOSES date string.

    Dates may omit the year ('13.10 - 15.12.26'); such a date takes the year of
    the next fully-qualified date, minus one if it would otherwise come after it.
    """
    toks = _DATE.findall(dt)
    parsed: list[tuple[int, int, int | None]] = []
    for t in toks:
        p = t.split(".")
        y = int(p[2]) + (2000 if len(p[2]) == 2 else 0) if len(p) == 3 and p[2] else None
        parsed.append((int(p[0]), int(p[1]), y))
    out = []
    for i, (d, m, y) in enumerate(parsed):
        if y is None:
            nxt = next(((dd, mm, yy) for dd, mm, yy in parsed[i + 1:] if yy), None)
            prv = next(((dd, mm, yy) for dd, mm, yy in reversed(parsed[:i]) if yy), None)
            if nxt:
                y = nxt[2] if (m, d) <= (nxt[1], nxt[0]) else nxt[2] - 1
            elif prv:
                y = prv[2] if (m, d) >= (prv[1], prv[0]) else prv[2] + 1
            else:
                continue
        iso = parse_de_date(f"{d}.{m}.{y}")
        if iso:
            out.append(iso)
    return (min(out), max(out)) if out else (None, None)


def _module_text(html: str) -> str | None:
    """'Modul: <title>' + Lernergebnisse + Lehrinhalte + teaching format."""
    soup = BeautifulSoup(html, "lxml")
    title = None
    for lab in soup.find_all("label"):
        if clean(lab.get_text()) == "Titel des Moduls":
            for t in lab.find_all_next(string=True):
                if t.strip() and clean(t) != "Titel des Moduls":
                    title = clean(t)
                    break
            break
    parts = [f"Modul: {title}"] if title else []
    for h in soup.find_all("h3"):
        head = clean(h.get_text())
        if head not in ("Lernergebnisse", "Lehrinhalte", "Beschreibung der Lehr- und Lernformen"):
            continue
        texts = []
        for sib in h.find_all_next():
            if sib.name == "h3":
                break
            if sib.name in ("p", "div", "li") and not sib.find(["div", "p", "li"]):
                t = clean(sib.get_text(" "))
                if t in ("Lernergebnisse", "Lehrinhalte", "Modulbestandteile",
                         "Beschreibung der Lehr- und Lernformen"):
                    continue
                if t and t not in texts:
                    texts.append(t)
        if texts:
            parts.append(f"{head}: " + " ".join(texts))
    return "\n\n".join(parts) if len(parts) > 1 else None
