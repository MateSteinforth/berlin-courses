"""UdK Berlin – HISinOne Vorlesungsverzeichnis (public, no login).

The catalog is a JSF tree whose nodes load via AJAX. We expand it with
Playwright (top-level sections split across several browser pages working in
parallel), collect course unitIds plus their breadcrumb, then open each
course's detail page for schedule + metadata and click the "Inhalte" tab for
the description. Descriptions are cached per unitId+period because fetching
them is the slow part.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from ..model import BIWEEKLY, BLOCK, IRREGULAR, SINGLE, WEEKLY, Course, Session
from ..util import CACHE, WEEKDAYS_DE, clean, load_json, parse_de_date, save_json
from . import CatalogSource

log = logging.getLogger(__name__)

HOST = "https://my.udk-berlin.de"
CATALOG = HOST + "/qisserver/pages/startFlow.xhtml?_flowId=showCourseCatalog-flow&periodId={pid}"
DETAIL = HOST + "/qisserver/pages/startFlow.xhtml?_flowId=detailView-flow&unitId={uid}&periodId={pid}"
CONTENT_CACHE = CACHE / "udk_contents.json"
WORKERS = 6


def _semester_map(html: str) -> dict[str, int]:
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for opt in soup.select("option[data-title]"):
        m = re.match(r"(Winter|Sommer)semester (\d{4})", opt["data-title"])
        if m:
            out[f"{m.group(2)}{'W' if m.group(1) == 'Winter' else 'S'}"] = int(opt["value"])
    return out


class UdkHisinone(CatalogSource):
    name = "udk_hisinone"
    university = "UdK"

    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page()
            pg.set_default_timeout(120_000)
            pg.goto(CATALOG.format(pid=""))
            sems = _semester_map(pg.content())
            if semester not in sems:
                log.info("UdK: semester %s not in catalog (%s)", semester, sems)
                browser.close()
                return []
            pid = sems[semester]
            pg.goto(CATALOG.format(pid=pid))
            pg.wait_for_load_state("networkidle")
            # top-level sections are the children of the root node
            top = pg.locator("button.treeTableIcon[aria-expanded='false']")
            n_top = top.count()
            browser.close()
        log.info("UdK %s (period %s): %d top-level sections", semester, pid, n_top)

        # Each worker thread owns its own Playwright instance + browser (the sync
        # API isn't shareable across threads) and expands a share of the sections.
        units: dict[str, list[str]] = {}
        lock = threading.Lock()

        def section_worker(idxs: list[int]):
            with sync_playwright() as p2:
                b2 = p2.chromium.launch()
                for i in idxs:
                    try:
                        _expand_section(b2, pid, i, limit, units, lock)
                    except Exception as e:
                        log.warning("UdK section %d failed: %s", i, e)
                b2.close()

        buckets = [list(range(n_top))[k::WORKERS] for k in range(WORKERS)]
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(section_worker, [b for b in buckets if b]))

        uids = sorted(units, key=int)
        if limit:
            uids = uids[:limit]
        log.info("UdK %s: %d courses, fetching details", semester, len(uids))

        cache = load_json(CONTENT_CACHE, {})
        results: dict[str, Course] = {}
        rlock = threading.Lock()

        def detail_worker(chunk: list[str]):
            with sync_playwright() as p3:
                b3 = p3.chromium.launch()
                page = b3.new_page()
                page.set_default_timeout(90_000)
                for uid in chunk:
                    try:
                        c = self._course(page, uid, pid, semester, units.get(uid, []), cache, rlock)
                        if c:
                            with rlock:
                                results[uid] = c
                    except Exception as e:
                        log.warning("UdK unit %s failed: %s", uid, e)
                b3.close()

        chunks = [uids[k::WORKERS] for k in range(WORKERS)]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            list(ex.map(detail_worker, [c for c in chunks if c]))
        log.info("UdK details done in %.0fs", time.time() - t0)
        save_json(CONTENT_CACHE, cache)
        return [results[u] for u in uids if u in results]

    # ---- detail ----------------------------------------------------------
    def _course(self, page, uid, pid, semester, path, cache, lock) -> Course | None:
        url = DETAIL.format(uid=uid, pid=pid)
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        soup = BeautifulSoup(page.content(), "lxml")
        fields = _label_fields(soup)
        title = fields.get("Titel")
        if not title:
            return None

        sessions, by_arr = _parse_appointments(soup)

        key = f"{pid}:{uid}"
        with lock:
            desc = cache.get(key)
        if desc is None:
            desc = ""
            tab = page.locator("button[id$='tabs:contentsTab']")
            if tab.count():
                _wait_idle(page)
                tab.first.click()
                _wait_idle(page)
                desc = _parse_contents(BeautifulSoup(page.content(), "lxml"))
            with lock:
                cache[key] = desc

        lecturers = fields.get("_lecturers") or []
        lang = None
        m = re.search(r"Unterrichtssprache\s*:?\s*(\w+)", desc or "")
        if m:
            lang = m.group(1)
        return Course(
            id=f"{self.name}:{uid}:{semester}",
            source=self.name,
            university=self.university,
            semester=semester,
            title=_strip_lecturer_prefix(title),
            url=url,
            type=fields.get("Veranstaltungsart"),
            number=fields.get("Nummer"),
            lecturers=lecturers,
            org=(fields.get("Organisationseinheit") or "").replace("(Verantwortlicher)", "").strip() or None,
            language=fields.get("Unterrichtssprache") or lang,
            description=desc or None,
            sessions=sessions,
            by_arrangement=by_arr and not sessions,
            catalog_path=path,
            extra={"raw_title": title} if _strip_lecturer_prefix(title) != title else {},
        )


def _expand_section(browser, pid, idx, limit, units, lock):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.set_default_timeout(120_000)
    page.goto(CATALOG.format(pid=pid))
    page.wait_for_load_state("networkidle")
    prefix = f"courseCatalog:0:{idx}:"
    sel = (f"button.treeTableIcon[aria-expanded='false'][id*='{prefix}'], "
           f"button.treeTableIcon[aria-expanded='false'][id$='courseCatalog:0:{idx}:t2g_0-{idx}']")
    clicks, t0 = 0, time.time()
    while True:
        btn = page.locator(sel)
        if btn.count() == 0 or (limit and clicks >= 10):
            break
        _wait_idle(page)
        try:
            btn.first.click(timeout=30_000)
        except Exception:
            _wait_idle(page)
            continue
        _wait_idle(page)
        clicks += 1
    found = _collect_units(page.content(), prefix)
    with lock:
        for uid, path in found.items():
            units.setdefault(uid, path)
    log.info("UdK section %d: %d expansions, %d courses, %.0fs", idx, clicks, len(found), time.time() - t0)
    ctx.close()


def _wait_idle(page):
    """HISinOne shows a #global-waiting curtain during AJAX; wait it out."""
    page.wait_for_load_state("networkidle")
    try:
        page.locator("#global-waiting").wait_for(state="hidden", timeout=30_000)
    except Exception:
        pass


def _collect_units(html: str, prefix: str) -> dict[str, list[str]]:
    """unitId -> breadcrumb of catalog node names."""
    soup = BeautifulSoup(html, "lxml")
    names: dict[str, str] = {}
    for tr in soup.select("tr[id$=':row']"):
        rid = tr["id"].rsplit(":row", 1)[0].split("courseCatalog:", 1)[-1]
        nm = tr.select_one(".treeElementName")
        if nm:
            # drop leading numbering inside the name cell
            names[rid] = clean(nm.get_text(" "))
    out = {}
    for a in soup.select("a.linkTableTree[href*='detailView-flow']"):
        m = re.search(r"unitId=(\d+)", a["href"])
        if not m:
            continue
        rid = a.get("id", "").split("courseCatalog:", 1)[-1].rsplit(":show", 1)[0]
        if prefix.split("courseCatalog:", 1)[-1] not in rid + ":":
            continue
        parts = rid.split(":")
        crumbs = []
        for i in range(2, len(parts)):
            nm = names.get(":".join(parts[:i]))
            if nm:
                crumbs.append(re.sub(r"^[\d.]+\s+", "", nm))
        out[m.group(1)] = crumbs
    return out


def _label_fields(soup) -> dict:
    out: dict = {}
    for line in soup.select("div.labelItemLine"):
        lab = line.find("label")
        ans = line.find(class_="answer")
        if not lab or not ans:
            continue
        key = clean(lab.get_text())
        if key and key.startswith("Verantwortliche"):
            names = [clean(li.get_text(" ")) for li in ans.select("li")]
            out["_lecturers"] = [n for n in names if n]
        elif key and key not in out:
            out[key] = clean(ans.get_text(" "))
    return out


_TIME = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")


def _parse_appointments(soup) -> tuple[list[Session], bool]:
    sessions: list[Session] = []
    by_arrangement = False
    for table in soup.select("table[id$='appointmentSeriesTableTable']"):
        # group name = legend/title of the enclosing parallel group fieldset
        grp_el = table.find_parent("fieldset")
        grp = None
        if grp_el:
            h = grp_el.find_previous(["h2", "h3"])
            grp = clean(h.get_text()) if h else None
        for resp in table.select("span[data-type=responsive]"):
            resp.decompose()  # mobile-layout header labels injected into each cell
        for tr in table.select("tbody tr"):
            if not tr.find("td"):
                continue
            cells = {(" ".join(td.get("class", []))): td for td in tr.find_all("td")}

            def col(n):
                td = cells.get(f"column{n}")
                return clean(td.get_text(" ")) if td else None

            rhythm_raw, wd, times, span = col(1) or "", col(2), col(3) or "", col(5) or ""
            note, room = col(7), col(9)
            low = rhythm_raw.lower()
            if "vereinbarung" in low or "vereinbarung" in times.lower():
                by_arrangement = True
                continue
            if "14" in low or "zweiwöch" in low:
                rhythm = BIWEEKLY
            elif "wöchentlich" in low:
                rhythm = WEEKLY
            elif "einzel" in low:
                rhythm = SINGLE
            elif "block" in low:
                rhythm = BLOCK
            else:
                rhythm = IRREGULAR
            tm = _TIME.search(times)
            dates = re.findall(r"\d{1,2}\.\d{1,2}\.\d{4}", span)
            first = parse_de_date(dates[0]) if dates else None
            last = parse_de_date(dates[-1]) if dates else first
            weekday = WEEKDAYS_DE.get((wd or "").lower()[:2]) if wd else None
            if weekday is None and rhythm in (SINGLE,) and first:
                from datetime import date
                weekday = date.fromisoformat(first).weekday()
            sessions.append(Session(
                weekday=weekday,
                start=tm.group(1).zfill(5) if tm else None,
                end=tm.group(2).zfill(5) if tm else None,
                rhythm=rhythm, first_date=first, last_date=last,
                room=room, building=udk_building(room) if room else None,
                group=grp, note=note,
            ))
    return sessions, by_arrangement


def _parse_contents(soup) -> str:
    """Text of the 'Inhalte' tab, as 'Heading: text' paragraphs."""
    parts = []
    for h2 in soup.select("h2"):
        title = clean(h2.get_text())
        box = h2.find_parent(class_=re.compile(r"box_title"))
        container = box.parent if box else None
        if not title or not container or title in ("Grunddaten", "Termine", "Semesterauswahl"):
            continue
        body = container.find(class_=re.compile(r"box_content"))
        if body is None or body.find("h2"):
            continue  # wrapper panel, the real sections are nested inside
        txt = clean(body.get_text(" ")) if body else None
        if txt and txt.startswith(title):
            txt = txt[len(title):].strip()
        if txt:
            parts.append(f"{title}: {txt}")
    return "\n\n".join(parts)


def _strip_lecturer_prefix(title: str) -> str:
    """UdK titles are often 'Lastname_Othername_Actual title'."""
    parts = title.split("_")
    if len(parts) > 1 and all(re.fullmatch(r"[A-ZÄÖÜŞ][\w\-ßäöüçşğ.'’ ]{1,30}", p) for p in parts[:-1]) \
            and len(parts[-1]) > 3:
        return parts[-1].strip()
    return title.strip()


def udk_building(room: str) -> str | None:
    """'Ha33 - 050' / 'Ha33 R. 069' / 'Lie - 212' -> 'UDK:Ha33' / 'UDK:Lie'."""
    m = re.search(r"\b([A-Z][a-zäöü]{0,4}\d{0,3}[a-z]?)\s*(?:-\s|R\.\s?)", room)
    if m:
        return f"UDK:{m.group(1)}"
    m = re.search(r"\b([A-Z][a-zäöü]{0,4}\d{1,3}[a-z]?)\b", room)
    return f"UDK:{m.group(1)}" if m else None
