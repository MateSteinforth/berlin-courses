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


# Course catalogs change around publication and the start of lectures, not
# continuously. Default refresh dates (month, day); sources may override.
DEFAULT_UPDATE_DATES = [(2, 1), (3, 15), (4, 10), (5, 5), (8, 1), (9, 15), (10, 10), (11, 5)]


class _Scheduled:
    update_dates: list[tuple[int, int]] = DEFAULT_UPDATE_DATES

    def next_update(self, after: date) -> date:
        """First scheduled refresh date strictly after `after`."""
        for y in (after.year, after.year + 1):
            for m, d in sorted(self.update_dates):
                cand = date(y, m, d)
                if cand > after:
                    return cand
        raise AssertionError("unreachable")


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
    from .lsf import HtwLsf, HuAgnes
    from .udk_sg import UdkStudiumGenerale
    return {s.name: s for s in (TuMoses(), UdkHisinone(), UdkStudiumGenerale(), FuVv(), HuAgnes(), HtwLsf())}


def lms_sources() -> dict[str, LmsSource]:
    from .moodle import TuIsis, UdkMoodle
    return {s.name: s for s in (TuIsis(), UdkMoodle())}
