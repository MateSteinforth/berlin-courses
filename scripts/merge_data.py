"""Merge the data/ changes of parallel crawl jobs into the checkout.

Each crawl job uploads the files it changed (same paths as in the repo). Raw files and
state files belong to one source, so they are copied. Cache files can be shared between
jobs (e.g. data/cache/ai_sessions.json), so JSON objects are merged key by key instead
of one job's version overwriting another's.

Usage: python scripts/merge_data.py <artifact dir> [...]
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for art in map(Path, sys.argv[1:]):
    for src in sorted(p for p in art.rglob("*") if p.is_file()):
        rel = src.relative_to(art)
        if rel.parts[0] != "data":
            continue
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if rel.parts[1] == "cache" and src.suffix == ".json" and dst.exists():
            try:
                old, new = json.loads(dst.read_text()), json.loads(src.read_text())
            except json.JSONDecodeError:
                old = new = None
            if isinstance(old, dict) and isinstance(new, dict):
                old.update(new)
                compact = not dst.read_text().startswith("{\n")
                dst.write_text(json.dumps(old, ensure_ascii=False, **({"separators": (",", ":")} if compact else {"indent": 1})))
                print(f"merged {rel}")
                continue
        shutil.copy2(src, dst)
        print(f"copied {rel}")
