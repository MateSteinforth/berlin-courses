"""CLI.

  python -m crawler crawl [--source tu_moses] [--semester 2026W] [--limit 20]
  python -m crawler lms   [--source tu_isis] [--limit 100]
  python -m crawler build [--ai] [--ai-max 50]
  python -m crawler list
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import date, datetime, timezone


from .util import DATA, RAW, load_json, save_json, semester_keys

STATE = DATA / "state.json"


def _due(state: dict, key: str) -> bool:
    nxt = state.get(key, {}).get("next_update")
    return nxt is None or date.fromisoformat(nxt) <= date.today()


def _mark(state: dict, key: str, source, count: int, semesters: list[str]) -> None:
    """Record a crawl; the next date comes from the university calendar.
    LMS sources span semesters, so they follow the earliest semester refresh."""
    today = date.today()
    nxt = min(source.next_update(sem, today, published=count > 0 or len(semesters) > 1) for sem in semesters)
    state[key] = {"crawled": today.isoformat(), "next_update": nxt.isoformat(), "count": count}
    # re-read so parallel crawl processes don't overwrite each other's entries
    disk = load_json(STATE, {})
    disk[key] = state[key]
    save_json(STATE, disk)


def main(argv=None) -> int:
    warnings.filterwarnings("ignore", module="bs4")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    log = logging.getLogger("crawler")
    ap = argparse.ArgumentParser(prog="crawler")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="crawl course catalogs")
    c.add_argument("--source", action="append")
    c.add_argument("--semester", action="append")
    c.add_argument("--limit", type=int)
    c.add_argument("--due", action="store_true", help="only crawl sources whose next update date has passed")
    m = sub.add_parser("lms", help="crawl Moodle/ISIS course spaces")
    m.add_argument("--source", action="append")
    m.add_argument("--limit", type=int)
    m.add_argument("--due", action="store_true")
    b = sub.add_parser("build", help="merge raw data into site/data")
    b.add_argument("--ai", action="store_true", help="call Claude for uncached courses")
    b.add_argument("--ai-max", type=int)
    sub.add_parser("list", help="list available sources")
    args = ap.parse_args(argv)

    from .sources import catalog_sources, lms_sources

    if args.cmd == "list":
        for name, s in {**catalog_sources(), **lms_sources()}.items():
            print(f"{name:16} {s.university}  {type(s).__doc__ or ''}".rstrip())
        return 0

    failed = False
    state = load_json(STATE, {})
    if args.cmd == "crawl":
        srcs = catalog_sources()
        for name in args.source or list(srcs):
            for sem in args.semester or semester_keys():
                key = f"{name}_{sem}"
                if args.due and not _due(state, key):
                    log.info("skip %s: next update %s", key, state[key]["next_update"])
                    continue
                log.info("== %s %s", name, sem)
                try:
                    courses = srcs[name].crawl(sem, limit=args.limit)
                except Exception:
                    log.exception("%s %s failed", name, sem)
                    failed = True
                    continue
                if not courses:
                    log.info("%s %s: nothing published", name, sem)
                    _mark(state, key, srcs[name], 0, [sem])
                    continue
                save_json(RAW / f"{name}_{sem}.json", {
                    "kind": "catalog", "source": name, "semester": sem,
                    "crawled": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                    "courses": [x.to_dict() for x in courses],
                })
                log.info("%s %s: saved %d courses", name, sem, len(courses))
                _mark(state, key, srcs[name], len(courses), [sem])
    elif args.cmd == "lms":
        srcs = lms_sources()
        for name in args.source or list(srcs):
            if args.due and not _due(state, name):
                log.info("skip %s: next update %s", name, state[name]["next_update"])
                continue
            try:
                courses = srcs[name].crawl(limit=args.limit)
            except Exception:
                log.exception("%s failed", name)
                failed = True
                continue
            save_json(RAW / f"{name}.json", {
                "kind": "lms", "source": name,
                "crawled": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "courses": [x.to_dict() for x in courses],
            })
            _mark(state, name, srcs[name], len(courses), semester_keys())
    elif args.cmd == "build":
        from .build import build
        build(ai=args.ai, ai_max=args.ai_max)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
