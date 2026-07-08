"""HowLongToBeat: horas estimadas para completar un juego.

HLTB no tiene API oficial; esto hace un POST a su endpoint de búsqueda interno.
Es FRÁGIL a propósito: si cambian su web puede dejar de funcionar, en cuyo caso
se devuelve None y el usuario mete las horas a mano en la ficha del juego."""
import logging

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://howlongtobeat.com/api/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://howlongtobeat.com/",
    "Origin": "https://howlongtobeat.com",
    "Content-Type": "application/json",
}


def _payload(title: str) -> dict:
    return {
        "searchType": "games",
        "searchTerms": title.split(),
        "searchPage": 1,
        "size": 5,
        "searchOptions": {
            "games": {
                "userId": 0, "platform": "", "sortCategory": "popular",
                "rangeCategory": "main", "rangeTime": {"min": None, "max": None},
                "gameplay": {"perspective": "", "flow": "", "genre": ""},
                "modifier": "",
            },
            "users": {"sortCategory": "postcount"},
            "filter": "", "sort": 0, "randomizer": 0,
        },
    }


def search_hours(title: str, year: int | None = None) -> float | None:
    """Horas de la historia principal (comp_main) del mejor resultado, o None."""
    if not title:
        return None
    try:
        resp = httpx.post(SEARCH_URL, json=_payload(title), headers=HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        # Preferir coincidencia por año si la hay; si no, el primer resultado
        best = data[0]
        if year:
            for g in data:
                if g.get("release_world") == year:
                    best = g
                    break
        seconds = best.get("comp_main") or best.get("comp_plus") or best.get("comp_100") or 0
        return round(seconds / 3600.0, 1) if seconds else None
    except Exception:
        logger.exception("Fallo al consultar HowLongToBeat para '%s'", title)
        return None
