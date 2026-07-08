"""Modelos de datos: items del catálogo (libros, películas, series, videojuegos,
podcasts), episodios (series/podcasts), etiquetas, listas y sagas."""
import enum
from datetime import datetime, date, timezone

from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, Date, Text, Table, Column,
    ForeignKey, UniqueConstraint, Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """UTC naive (compatible con las filas ya guardadas); evita datetime.utcnow(), deprecado en 3.12."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _by_value(enum_cls):
    """Fuerza a SQLAlchemy a guardar el .value del enum (no el .name)."""
    return [e.value for e in enum_cls]


class MediaType(enum.Enum):
    LIBRO = "libro"
    PELICULA = "pelicula"
    SERIE = "serie"
    VIDEOJUEGO = "videojuego"
    PODCAST = "podcast"


# Tipos que se siguen por episodios (temporada + episodio)
EPISODIC_TYPES = (MediaType.SERIE, MediaType.PODCAST)


class MediaStatus(enum.Enum):
    WISHLIST = "wishlist"        # lo quiero, aún no disponible / no lo tengo
    PENDIENTE = "pendiente"      # disponible, por empezar (backlog)
    EN_PROGRESO = "en_progreso"
    COMPLETADO = "completado"
    ABANDONADO = "abandonado"


class Priority(enum.Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


media_item_tags = Table(
    "media_item_tags",
    Base.metadata,
    Column("media_item_id", ForeignKey("media_items.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)

list_items = Table(
    "list_items",
    Base.metadata,
    Column("list_id", ForeignKey("listas.id"), primary_key=True),
    Column("media_item_id", ForeignKey("media_items.id"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Lista(Base):
    """Lista/colección manual del usuario ('para ver con pareja', 'top 2026'...)."""

    __tablename__ = "listas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["MediaItem"]] = relationship(secondary=list_items, order_by="MediaItem.title")


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, values_callable=_by_value))
    title: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    external_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # tmdb|openlibrary|rawg|imdb|itunes
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)  # autor / director / estudio
    overview: Mapped[str] = mapped_column(Text, default="")
    cast: Mapped[str | None] = mapped_column(Text, nullable=True)  # reparto principal, separado por coma

    status: Mapped[MediaStatus] = mapped_column(
        SAEnum(MediaStatus, values_callable=_by_value), default=MediaStatus.PENDIENTE
    )
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, values_callable=_by_value), default=Priority.MEDIA
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-10
    notes: Mapped[str] = mapped_column(Text, default="")

    # Progreso genérico: "página 120/300", "45%", "temporada 2 episodio 5" (manual) o derivado de episodios
    progress_current: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_unit: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Duración para stats "tiempo total" y sugerencia "tengo 2h" (se rellena en fase 2)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # peli o duración media de episodio
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)       # libros
    hltb_hours: Mapped[float | None] = mapped_column(Float, nullable=True)        # juegos (HowLongToBeat)

    completed_at: Mapped[date | None] = mapped_column(Date, nullable=True)  # fecha de completado (estadísticas)
    genres: Mapped[str | None] = mapped_column(String(255), nullable=True)  # géneros separados por coma

    # Saga/franquicia: nombre editable (manual) + id de colección de TMDB (automático)
    saga: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tmdb_collection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Estrenos: fecha de lanzamiento (pelis/juegos) y si ya se avisó de su salida
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    tags: Mapped[list["Tag"]] = relationship(secondary=media_item_tags)
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
        order_by="Episode.season_number, Episode.episode_number",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def is_episodic(self) -> bool:
        return self.media_type in EPISODIC_TYPES

    def episode_stats(self) -> dict:
        """Cuenta de episodios vistos/totales y el próximo por ver (para series/podcasts)."""
        eps = self.episodes
        total = len(eps)
        watched = sum(1 for e in eps if e.watched)
        next_ep = None
        for e in eps:  # ya vienen ordenados por temporada/episodio
            if not e.watched:
                next_ep = e
                break
        return {"total": total, "watched": watched, "next": next_ep}


class Episode(Base):
    """Episodio de una serie o podcast. La posición de la serie (progreso, próximo
    episodio) se deriva de qué episodios están marcados como vistos."""

    __tablename__ = "episodes"
    __table_args__ = (
        UniqueConstraint("item_id", "season_number", "episode_number", name="uq_episode_item_season_ep"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("media_items.id"), index=True)
    item: Mapped["MediaItem"] = relationship(back_populates="episodes")

    season_number: Mapped[int] = mapped_column(Integer, default=1)
    episode_number: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    overview: Mapped[str] = mapped_column(Text, default="")
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Si ya se avisó (Telegram) de que este episodio se estrenó
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def code(self) -> str:
        """Código legible tipo S02E05."""
        return "S%02dE%02d" % (self.season_number, self.episode_number)
