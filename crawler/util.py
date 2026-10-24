from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CACHE = DATA / "cache"
SITE_DATA = ROOT / "site" / "data"

USER_AGENT = (
    "berlin-courses/0.1 (+https://github.com/MateSteinforth/berlin-courses; "
    "student course-catalog aggregator)"
)

WEEKDAYS_DE = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept-Language"] = "de,en;q=0.8"
    return s


def get(s: requests.Session, url: str, retries: int = 3, **kw) -> requests.Response:
    for attempt in range(retries):
        try:
            r = s.get(url, timeout=60, **kw)
            if r.status_code < 500:
                return r
        except requests.RequestException:
            if attempt == retries - 1:
                raise
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    return r


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    t = re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()
    return t or None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if compact:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    else:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False))
    tmp.replace(path)


def parse_de_date(s: str, default_year: int | None = None) -> str | None:
    """'13.10.26' / '13.10.2026' / '13.10' -> ISO date."""
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?", s.strip())
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
    if y is None:
        if default_year is None:
            return None
        year = default_year
    else:
        year = int(y) + (2000 if len(y) == 2 else 0)
    try:
        return date(year, mo, d).isoformat()
    except ValueError:
        return None


def semester_keys(today: date | None = None) -> list[str]:
    """Current semester and the next one, as '2026W' / '2027S'.

    Summer semester: Apr–Sep, winter semester: Oct–Mar (named by starting year).
    """
    t = today or date.today()
    if 4 <= t.month <= 9:
        cur = f"{t.year}S"
        nxt = f"{t.year}W"
    else:
        y = t.year if t.month >= 10 else t.year - 1
        cur = f"{y}W"
        nxt = f"{y + 1}S"
    return [cur, nxt]


def semester_label(key: str) -> str:
    y, k = int(key[:4]), key[4]
    return f"SoSe {y}" if k == "S" else f"WiSe {y}/{str(y + 1)[2:]}"


def env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None
