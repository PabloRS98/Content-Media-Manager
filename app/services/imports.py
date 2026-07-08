"""Importadores de CSV: Goodreads/StoryGraph (libros) y Backloggd/genérico (juegos).

Tolerantes con los nombres de columna (cada export usa los suyos). Dedupe por
título+autor/estudio contra lo ya existente del mismo tipo."""
import csv
import io
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import MediaItem, MediaStatus, MediaType

logger = logging.getLogger(__name__)


def _get(row: dict, *names: str) -> str:
    """Primer valor no vacío entre las columnas candidatas (case-insensitive)."""
    lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    for n in names:
        v = lowered.get(n.lower())
        if v:
            return v
    return ""


def _int(value: str):
    try:
        return int(float(value)) if value else None
    except (ValueError, TypeError):
        return None


def _rating5_to_10(value: str):
    """Convierte una nota en escala 0-5 (con medias) a 1-10; 0/vacío -> None."""
    try:
        r = float(value)
    except (ValueError, TypeError):
        return None
    if r <= 0:
        return None
    return max(1, min(10, round(r * 2)))


def _parse_date(value: str):
    if not value:
        return None
    value = value.split("/")[0].strip() if value.count("/") > 2 else value.strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


_BOOK_STATUS = {
    "read": MediaStatus.COMPLETADO,
    "currently-reading": MediaStatus.EN_PROGRESO,
    "currently reading": MediaStatus.EN_PROGRESO,
    "reading": MediaStatus.EN_PROGRESO,
    "to-read": MediaStatus.PENDIENTE,
    "to read": MediaStatus.PENDIENTE,
    "did-not-finish": MediaStatus.ABANDONADO,
    "did not finish": MediaStatus.ABANDONADO,
    "dnf": MediaStatus.ABANDONADO,
}

_GAME_STATUS = {
    "completed": MediaStatus.COMPLETADO,
    "beaten": MediaStatus.COMPLETADO,
    "mastered": MediaStatus.COMPLETADO,
    "playing": MediaStatus.EN_PROGRESO,
    "played": MediaStatus.EN_PROGRESO,
    "backlog": MediaStatus.PENDIENTE,
    "wishlist": MediaStatus.WISHLIST,
    "abandoned": MediaStatus.ABANDONADO,
    "retired": MediaStatus.ABANDONADO,
    "shelved": MediaStatus.ABANDONADO,
    "dropped": MediaStatus.ABANDONADO,
}


def _existing_keys(db: Session, media_type: MediaType) -> set:
    return {
        (i.title.lower(), (i.creator or "").lower())
        for i in db.query(MediaItem).filter(MediaItem.media_type == media_type).all()
    }


def import_books_csv(db: Session, text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    fields = " ".join(reader.fieldnames or []).lower()
    source = "storygraph" if "read status" in fields else "goodreads"
    existing = _existing_keys(db, MediaType.LIBRO)

    creados = duplicados = omitidos = 0
    for row in reader:
        title = _get(row, "Title")
        if not title:
            omitidos += 1
            continue
        author = _get(row, "Author", "Authors", "Primary Author") or None
        key = (title.lower(), (author or "").lower())
        if key in existing:
            duplicados += 1
            continue

        status = _BOOK_STATUS.get(_get(row, "Exclusive Shelf", "Read Status", "Status").lower(), MediaStatus.PENDIENTE)
        fecha = _parse_date(_get(row, "Date Read", "Last Date Read", "Dates Read"))
        db.add(MediaItem(
            media_type=MediaType.LIBRO,
            title=title,
            creator=author,
            external_source=source,
            rating=_rating5_to_10(_get(row, "My Rating", "Star Rating", "Rating")),
            page_count=_int(_get(row, "Number of Pages", "Pages", "Page Count")),
            year=_int(_get(row, "Original Publication Year", "Year Published", "Year")),
            status=status,
            completed_at=fecha if status == MediaStatus.COMPLETADO else None,
        ))
        existing.add(key)
        creados += 1
    db.commit()
    return {"creados": creados, "duplicados": duplicados, "omitidos": omitidos}


def import_games_csv(db: Session, text: str) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    existing = _existing_keys(db, MediaType.VIDEOJUEGO)

    creados = duplicados = omitidos = 0
    for row in reader:
        title = _get(row, "Title", "Name", "Game")
        if not title:
            omitidos += 1
            continue
        key = (title.lower(), "")
        if key in existing:
            duplicados += 1
            continue

        status = _GAME_STATUS.get(_get(row, "Status", "Shelf").lower(), MediaStatus.PENDIENTE)
        hours = _get(row, "Hours", "Playtime", "Time")
        try:
            hltb = float(hours) if hours else None
        except ValueError:
            hltb = None
        fecha = _parse_date(_get(row, "Date", "Completed", "Date Completed"))
        db.add(MediaItem(
            media_type=MediaType.VIDEOJUEGO,
            title=title,
            creator=_get(row, "Developer", "Studio", "Platforms", "Platform") or None,
            external_source="import",
            rating=_rating5_to_10(_get(row, "Rating", "Score", "My Rating")),
            hltb_hours=hltb,
            year=_int(_get(row, "Release Date", "Year", "Released")),
            status=status,
            completed_at=fecha if status == MediaStatus.COMPLETADO else None,
        ))
        existing.add(key)
        creados += 1
    db.commit()
    return {"creados": creados, "duplicados": duplicados, "omitidos": omitidos}
