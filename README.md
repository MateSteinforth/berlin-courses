# Berlin Courses

One searchable list of the courses at Berlin universities (TU, UdK, FU, HU, HTW, KHB, ASH, FH Potsdam, …),
filterable by **when you're free** and **how far it is** from home or work by
foot, bike, e-scooter or BVG.

Built for students who can take courses anywhere in Berlin (e.g. the
Design & Computation master at UdK/TU) but have a life around it.

## How it works

```
crawler/               Python, runs in GitHub Actions
  sources/             one plugin per catalog (MOSES, HISinOne, FU VV, LSF, Moodle, …)
  locations.py         building codes -> addresses -> coordinates (OSM Nominatim, cached)
  enrich.py            Claude Haiku: English summary, topic tags, D+C relevance,
                       free-text date extraction (cached, only new courses cost money)
  build.py             merge everything -> site/data/*.json
data/raw/              crawled data, committed (the cache between runs)
data/cache/            geocoding, AI results, descriptions
data/state.json        per source: last crawl + next scheduled update
site/                  static frontend (GitHub Pages), no build step
```

**Update schedule.** Catalogs change around publication and the start of
lectures, not weekly. `crawler/calendars.py` holds every university's lecture
periods and catalog publication dates (researched, with sources). Each
semester is crawled at publication (re-checked weekly until it appears),
2 weeks before lectures, 1 week after the start and 5 weeks after the start
(blocks get added late). The workflow runs daily but only crawls what's due
(`data/state.json`); everything else is served from `data/raw`. Update
`calendars.py` once a year; unknown semesters fall back to the usual pattern.
Force a crawl from the Actions tab ("Run workflow" → force).

**Privacy.** Home/work addresses, free time and favourites are stored only in
your browser (localStorage). Addresses are geocoded via OpenStreetMap from the
browser; travel times come from OSRM (walk/bike/scooter) and the BVG REST API
(transit, with an estimate when the API is down).

## Run locally

```bash
uv venv && . .venv/bin/activate          # or python -m venv .venv
pip install -r requirements.txt
python -m playwright install chromium

python -m crawler list                   # available sources
python -m crawler crawl --source tu_moses --limit 20
python -m crawler lms                    # ISIS + UdK Moodle
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env   # optional, gitignored
python -m crawler build --ai
python -m http.server -d site 8000       # open http://localhost:8000
```

## GitHub setup

- Settings → Pages → Source: **GitHub Actions**
- Settings → Secrets → Actions: `ANTHROPIC_API_KEY` (optional; tags, summaries, Studium Generale dates).
  `UDK_USER`/`UDK_PASS`/`TU_USER`/`TU_PASS` are wired in but not needed today.

See [UNIVERSITIES.md](UNIVERSITIES.md) for coverage and how to add a university.
