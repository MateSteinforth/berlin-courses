"""CLI.

  python -m crawler crawl [--source tu_moses] [--semester 2026W] [--limit 20]
  python -m crawler lms   [--source tu_isis] [--limit 100]
  python -m crawler build [--ai] [--ai-max 50] [--ai-unis UdK,TU]
  python -m crawler list
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import date, datetime, timezone


from .util import RAW, load_json, load_state, save_json, save_state, semester_keys


def _due(state: dict, key: str) -> bool:
    nxt = state.get(key, {}).get("next_update")
    return nxt is None or date.fromisoformat(nxt) <= date.today()


def _mark(state: dict, key: str, source, count: int, semesters: list[str]) -> None:
    """Record a crawl; the next date comes from the university calendar.
    LMS sources span semesters, so they follow the earliest semester refresh."""
    today = date.today()
    nxt = min(source.next_update(sem, today, published=count > 0 or len(semesters) > 1) for sem in semesters)
    state[key] = {"crawled": today.isoformat(), "next_update": nxt.isoformat(), "count": count}
    save_state(key, state[key])   # one file per key: parallel crawl jobs never touch the same file


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
    b.add_argument("--ai-wait", type=int, default=120, help="minutes to wait for AI batches (0 = submit only)")
    b.add_argument("--ai-unis", help="only call the AI for these universities, comma-separated (default: all)")
    sub.add_parser("list", help="list available sources")
    d = sub.add_parser("due", help="JSON list of sources to crawl now (for the workflow's job matrix)")
    d.add_argument("--force", action="store_true", help="every source, ignoring the schedule")
    d.add_argument("--source", action="append", help="only these sources")
    args = ap.parse_args(argv)

    from .sources import catalog_sources, lms_sources

    if args.cmd == "list":
        for name, s in {**catalog_sources(), **lms_sources()}.items():
            print(f"{name:16} {s.university}  {type(s).__doc__ or ''}".rstrip())
        return 0

    if args.cmd == "due":
        import json
        st = load_state()
        jobs = [{"name": n, "kind": "catalog"} for n in catalog_sources()
                if args.force or any(_due(st, f"{n}_{sem}") for sem in semester_keys())]
        jobs += [{"name": n, "kind": "lms"} for n in lms_sources() if args.force or _due(st, n)]
        if args.source:
            jobs = [j for j in jobs if j["name"] in args.source]
        print(json.dumps(jobs))
        return 0

    failed = False
    state = load_state()
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
                # a broken crawl (site hiccup, stuck paginator) must not wipe good data
                prev = len(load_json(RAW / f"{name}_{sem}.json", {}).get("courses", []))
                if not args.limit and prev >= 100 and len(courses) < prev * 0.6:
                    log.error("%s %s: only %d courses vs %d last time – keeping the old file", name, sem, len(courses), prev)
                    failed = True
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
        unis = [u.strip() for u in (args.ai_unis or "").split(",") if u.strip() and u.strip() != "all"]
        build(ai=args.ai, ai_max=args.ai_max, ai_unis=unis or None, ai_wait=args.ai_wait)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
