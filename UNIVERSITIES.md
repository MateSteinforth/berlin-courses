# Berlin universities and their course catalogs

Status of each institution in this project. "Module" is the crawler plugin in
`crawler/sources/`. Students of most Berlin universities can take courses at the
others (Nebenhörer / Gasthörer / BUA cross-registration), so the aim is to cover
everything that publishes a catalog.

## Covered

| Institution | Catalog system | Access | Module | Descriptions |
|---|---|---|---|---|
| **TU Berlin** | MOSES (`moseskonto.tu-berlin.de`) | public | `tu_moses` | module descriptions (Lernergebnisse, Lehrinhalte) via the course's module tab |
| **TU Berlin** | ISIS (Moodle) | guest-browsable | `tu_isis` (LMS) | course summaries; linked to MOSES courses by title |
| **UdK Berlin** | HISinOne (`my.udk-berlin.de`) | public | `udk_hisinone` | "Inhalte" tab |
| **UdK Berlin** | Studium Generale web pages | public | `udk_sg` | full page text; dates extracted by Claude Haiku |
| **UdK Berlin** | Moodle | guest-browsable | `udk_moodle` (LMS) | course summaries |
| **FU Berlin** | Vorlesungsverzeichnis `fu-berlin.de/vv` | public | `fu_vv` | "Kommentar" |
| **HU Berlin** | AGNES (HIS-LSF) | public | `hu_agnes` | "Kommentar" |
| **HTW Berlin** | HIS-LSF `lsf.htw-berlin.de` | public | `htw_lsf` | "Kommentar" (where filled in) |
| **weißensee kunsthochschule (KHB)** | Incom `see.incom.org/vlv/<year>-<1\|2>` | public | `khb_incom` | workspace "Info" text; block dates extracted by Claude Haiku |
| **ASH (Alice Salomon Hochschule)** | HIS-LSF `ash-berlin.eu/qisserver` | public | `ash_lsf` | "Kommentar" (where filled in) |

## Not yet covered

| Institution | What we know | Next step |
|---|---|---|
| **BHT (Berliner Hochschule für Technik)** | Runs **HISinOne** at `polli.bht-berlin.de` (same system as UdK). The catalog is public but for WiSe 26/27 says "wird gerade aufgebaut" (still being built, empty). Public lecturer and room timetables exist in the same system. | Generalize `udk_hisinone` to take a host and enable it once the catalog is filled; until then a room-timetable crawler could supply times (descriptions from the module handbook PDFs). |
| **HWR Berlin** | Moved to **S.A.M.** (academyFIVE) at `sam.hwr-berlin.de` (Campus4u retired). Its public catalog lists 1,165 WiSe 2026 entries (50 per page, `?page=N`), but they're modules only, with title, credits and lecturer. Dates and rooms aren't public. | Low relevance for D+C (business, law, public administration). Could list modules without times, or crawl with a login. |
| **HfM Hanns Eisler** | Music conservatory, website only. | Low priority. |
| **HfS Ernst Busch** | Acting/directing school, website only. | Low priority. |
| **EHB / KHSB** | Church-run universities of applied sciences. | Low priority. |
| **Charité** | Medicine; teaching is not open to external students. | Skip. |
| Private (Bard College Berlin, Hertie School, ESMT, CODE University, …) | Mostly not open to students of other universities. | Skip unless a cooperation exists. |

## Adding a university

1. Create `crawler/sources/<name>.py` with a `CatalogSource` subclass that returns
   `Course` objects for a semester key (`2026W`, `2027S`). If the site lists
   individual dates, `model.sessions_from_dates()` turns them into weekly series,
   blocks and single dates.
2. Register it in `crawler/sources/__init__.py`.
3. If rooms carry street addresses, return `ADDR:<street>` as the building key;
   otherwise add building codes to `crawler/locations.py`.
4. Optional: override `update_dates` if the catalog publishes on a different rhythm.
5. Run `python -m crawler crawl --source <name> --limit 20` and check the output in `data/raw/`.

An HIS-LSF university only needs a subclass of `LsfSource` with `base` and
`root` (see `HuAgnes` / `HtwLsf` in `crawler/sources/lsf.py`).
