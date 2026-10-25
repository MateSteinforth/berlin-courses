"""Source plugins.

To add a university, create a module here with a subclass of `CatalogSource`
(or `LmsSource` for Moodle-style course spaces) and register it in SOURCES.
A source only has to turn its website into `Course` objects; locations, AI
tagging and the frontend are handled generically.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import date

from ..model import Course, LmsCourse

log = logging.getLogger(__name__)


class _Scheduled:
    """Recrawl dates come from the university's academic calendar (calendars.py)."""
    university: str = ""

    def next_update(self, semester: str, after: date, published: bool = True) -> date:
        from ..calendars import next_update
        return next_update(self.university, semester, after, published)


class CatalogSource(_Scheduled, ABC):
    name: str = ""          # plugin id, used in file names and course ids
    university: str = ""    # display name ("TU", "UdK")

    @abstractmethod
    def crawl(self, semester: str, limit: int | None = None) -> list[Course]:
        """Return all courses for a semester key like '2026W'.

        Return an empty list if the semester isn't published yet.
        `limit` caps the number of courses (for quick local test runs).
        """


class LmsSource(_Scheduled, ABC):
    name: str = ""
    university: str = ""

    @abstractmethod
    def crawl(self, limit: int | None = None) -> list[LmsCourse]:
        ...


def catalog_sources() -> dict[str, CatalogSource]:
    from .tu_moses import TuMoses
    from .udk_hisinone import UdkHisinone
    from .fu_vv import FuVv
    from .incom import FhpIncom, KhbIncom
    from .lsf import AshLsf, HtwLsf, HuAgnes
    from .manual import Manual
    from .udk_sg import UdkStudiumGenerale
    return {s.name: s for s in (TuMoses(), UdkHisinone(), UdkStudiumGenerale(), FuVv(), HuAgnes(),
                                HtwLsf(), KhbIncom(), AshLsf(), FhpIncom(), Manual())}


def lms_sources() -> dict[str, LmsSource]:
    from .moodle import TuIsis, UdkMoodle
    return {s.name: s for s in (TuIsis(), UdkMoodle())}
