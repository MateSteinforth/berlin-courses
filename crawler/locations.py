"""Building codes -> addresses -> coordinates.

Sources give rooms like 'Charlottenburg, MA 001' (TU) or 'Ha33 - 050' (UdK).
Each source normalizes these to a building key ('TU:Charlottenburg/MA',
'UDK:Ha33'). Here we map keys to addresses (curated table below, extend it
when the build log reports unknown buildings) and geocode addresses once via
OpenStreetMap Nominatim, caching the result in data/cache/geocode.json.

Coordinates for well-known buildings are pinned directly so the site works
even if geocoding is unavailable.
"""
from __future__ import annotations

import logging
import re
import time

from .util import CACHE, USER_AGENT, load_json, save_json, session

log = logging.getLogger(__name__)
GEOCACHE = CACHE / "geocode.json"

# --- TU Berlin -------------------------------------------------------------
# key: building code as it appears after the campus prefix in MOSES
TU_BUILDINGS: dict[str, tuple[str, str]] = {
    # code: (name, address)
    "H": ("TU Hauptgebäude", "Straße des 17. Juni 135, 10623 Berlin"),
    "HE": ("TU Hauptgebäude Erweiterung", "Straße des 17. Juni 135, 10623 Berlin"),
    "MA": ("TU Mathematikgebäude", "Straße des 17. Juni 136, 10623 Berlin"),
    "EB": ("TU Erweiterungsbau", "Straße des 17. Juni 145, 10623 Berlin"),
    "A": ("TU Architekturgebäude", "Straße des 17. Juni 152, 10623 Berlin"),
    "C": ("TU Chemiegebäude", "Straße des 17. Juni 115, 10623 Berlin"),
    "TC": ("TU Technische Chemie", "Straße des 17. Juni 124, 10623 Berlin"),
    "BH": ("TU Bergbau und Hüttenwesen", "Ernst-Reuter-Platz 1, 10587 Berlin"),
    "TEL": ("TU Telefunken-Hochhaus", "Ernst-Reuter-Platz 7, 10587 Berlin"),
    "ER": ("TU Ernst-Ruska-Gebäude", "Hardenbergstraße 36, 10623 Berlin"),
    "EW": ("TU Eugene-Paul-Wigner-Gebäude", "Hardenbergstraße 36, 10623 Berlin"),
    "E": ("TU Elektrotechnik", "Einsteinufer 17, 10587 Berlin"),
    "E-N": ("TU Elektrotechnik Neubau", "Einsteinufer 17, 10587 Berlin"),
    "EN": ("TU Elektrotechnik Neubau", "Einsteinufer 17, 10587 Berlin"),
    "HFT": ("TU Hochfrequenztechnik", "Einsteinufer 25, 10587 Berlin"),
    "HL": ("TU Hochspannungslabor", "Einsteinufer 11, 10587 Berlin"),
    "F": ("TU Flugtechnik", "Marchstraße 12, 10587 Berlin"),
    "MAR": ("TU Marchstraße", "Marchstraße 23, 10587 Berlin"),
    "FH": ("TU Fraunhofer-Haus", "Fraunhoferstraße 33, 10587 Berlin"),
    "PTZ": ("TU Produktionstechnisches Zentrum", "Pascalstraße 8, 10587 Berlin"),
    "HBS": ("TU Hardenbergstraße", "Hardenbergstraße 16, 10623 Berlin"),
    "TIB": ("TU Campus Wedding (TIB)", "Gustav-Meyer-Allee 25, 13355 Berlin"),
    "ACK": ("TU Ackerstraße", "Ackerstraße 76, 13355 Berlin"),
}

# Fallback per MOSES campus label when the building code is unknown
TU_CAMPUS: dict[str, tuple[str, float, float]] = {
    "Charlottenburg": ("TU Campus Charlottenburg", 52.5125, 13.3266),
    "Wedding": ("TU Campus Wedding", 52.5421, 13.3843),
    "Dahlem": ("TU Campus Dahlem", 52.4573, 13.2866),
}

# --- UdK Berlin --------------------------------------------------------------
UDK_BUILDINGS: dict[str, tuple[str, str]] = {
    "Ha33": ("UdK Hardenbergstraße 33", "Hardenbergstraße 33, 10623 Berlin"),
    "Ha41": ("UdK Hardenbergstraße 41", "Hardenbergstraße 41, 10623 Berlin"),
    "Fa1b": ("UdK Fasanenstraße 1b", "Fasanenstraße 1b, 10623 Berlin"),
    "Fa88": ("UdK Fasanenstraße 88", "Fasanenstraße 88, 10623 Berlin"),
    "Lie": ("UdK Lietzenburger Straße 45", "Lietzenburger Straße 45, 10789 Berlin"),
    "Bu": ("UdK Bundesallee 1-12", "Bundesallee 1, 10719 Berlin"),
    "Ba1": ("UdK Bundesallee 1-12", "Bundesallee 1, 10719 Berlin"),
    "E43": ("UdK Einsteinufer 43", "Einsteinufer 43, 10587 Berlin"),
    "Ei43": ("UdK Einsteinufer 43", "Einsteinufer 43, 10587 Berlin"),
    "Gru": ("UdK Medienhaus Grunewaldstraße", "Grunewaldstraße 2, 10823 Berlin"),
    "Gr2": ("UdK Medienhaus Grunewaldstraße", "Grunewaldstraße 2, 10823 Berlin"),
    "Mi30": ("UdK Mierendorffstraße 30", "Mierendorffstraße 30, 10589 Berlin"),
    "Str17": ("UdK Straße des 17. Juni 118", "Straße des 17. Juni 118, 10623 Berlin"),
}

# Street prefixes for codes like 'Ha33' / 'Fa1b' that aren't in the table yet
UDK_STREETS = {
    "Ha": "Hardenbergstraße", "Fa": "Fasanenstraße", "Lie": "Lietzenburger Straße",
    "Bu": "Bundesallee", "Ba": "Bundesallee", "Ei": "Einsteinufer", "E": "Einsteinufer",
    "Gru": "Grunewaldstraße", "Gr": "Grunewaldstraße", "Mi": "Mierendorffstraße",
    "Str": "Straße des 17. Juni",
}

# Pinned coordinates (lat, lon) so common buildings never depend on geocoding.
PINNED: dict[str, tuple[float, float]] = {
    "Straße des 17. Juni 135, 10623 Berlin": (52.51259, 13.32694),
    "Straße des 17. Juni 136, 10623 Berlin": (52.51368, 13.32539),
    "Hardenbergstraße 33, 10623 Berlin": (52.50948, 13.32724),
    "Einsteinufer 43, 10587 Berlin": (52.51714, 13.32373),
    "Lietzenburger Straße 45, 10789 Berlin": (52.50108, 13.33229),
    "Fasanenstraße 1b, 10623 Berlin": (52.50937, 13.32813),
    "Bundesallee 1, 10719 Berlin": (52.50215, 13.32970),
    "Grunewaldstraße 2, 10823 Berlin": (52.49013, 13.35246),
    "Gustav-Meyer-Allee 25, 13355 Berlin": (52.54208, 13.38437),
}


def describe(key: str) -> tuple[str, str | None, str | None]:
    """building key -> (display name, address or None, campus fallback key or None)."""
    uni, _, rest = key.partition(":")
    if uni == "ADDR":  # source already gave a street address
        city = "" if re.search(r"\b\d{5}\b", rest) else ", Berlin"
        return rest, f"{rest}{city}", None
    if uni == "TU":
        campus, _, code = rest.partition("/")
        if code and code in TU_BUILDINGS:
            name, addr = TU_BUILDINGS[code]
            return name, addr, campus
        return (f"TU {campus} {code}".strip(), None, campus)
    if uni == "UDK":
        if rest in UDK_BUILDINGS:
            name, addr = UDK_BUILDINGS[rest]
            return name, addr, None
        m = re.match(r"([A-Za-zÄÖÜäöü]+?)(\d+[a-z]?)$", rest)
        if m and m.group(1) in UDK_STREETS:
            street = UDK_STREETS[m.group(1)]
            return f"UdK {street} {m.group(2)}", f"{street} {m.group(2)}, Berlin", None
        return f"UdK {rest}", None, None
    return key, None, None


def geocode(addresses: set[str]) -> dict[str, tuple[float, float] | None]:
    cache = load_json(GEOCACHE, {})
    s = session()
    s.headers["User-Agent"] = USER_AGENT
    out = {}
    for addr in sorted(addresses):
        if addr in PINNED:
            out[addr] = PINNED[addr]
            continue
        if addr in cache:
            out[addr] = tuple(cache[addr]) if cache[addr] else None
            continue
        try:
            r = s.get("https://nominatim.openstreetmap.org/search",
                      params={"q": addr, "format": "jsonv2", "limit": 1, "countrycodes": "de"},
                      timeout=30)
            res = r.json()
            ll = (round(float(res[0]["lat"]), 5), round(float(res[0]["lon"]), 5)) if res else None
        except Exception as e:  # network trouble: try again next run
            log.warning("geocode failed for %s: %s", addr, e)
            out[addr] = None
            continue
        cache[addr] = list(ll) if ll else None
        out[addr] = ll
        time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
    save_json(GEOCACHE, cache)
    return out


def resolve(keys: set[str]) -> dict[str, dict]:
    """building keys -> location records {name, address, lat, lon, approx}."""
    described = {k: describe(k) for k in keys}
    coords = geocode({addr for _, addr, _ in described.values() if addr})
    out, unknown = {}, []
    for k, (name, addr, campus) in described.items():
        ll = coords.get(addr) if addr else None
        approx = False
        if not ll and campus and campus in TU_CAMPUS:
            cname, lat, lon = TU_CAMPUS[campus]
            ll, approx = (lat, lon), True
        if not ll:
            unknown.append(k)
            continue
        out[k] = {"name": name, "address": addr, "lat": ll[0], "lon": ll[1], "approx": approx}
    if unknown:
        log.info("locations: %d unknown building keys (add to locations.py): %s",
                 len(unknown), ", ".join(sorted(unknown)[:60]))
    return out
