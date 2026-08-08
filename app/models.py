"""Modelos de datos: items del catálogo (libros, películas, series, videojuegos,
podcasts), episodios (series/podcasts), etiquetas, listas y sagas."""
import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """UTC naive (compatible con las filas ya guardadas); evita datetime.utcnow(), deprecado en 3.12."""
    return datetime.now(UTC).replace(tzinfo=None)


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


class Usuario(Base):
    """Una persona de la casa. Cada una tiene su propio catálogo.

    La contraseña es OPCIONAL: una cuenta sin contraseña entra de un clic desde
    el selector, que es lo normal en un servidor doméstico. Las que sí la
    tienen quedan cerradas de verdad -- sin ella no se entra ni se ven sus
    datos-- porque el punto de tenerla es la privacidad, no un recordatorio.

    `password_hash` guarda `scrypt$<sal>$<hash>` (ver `app/cuentas.py`). Nunca
    la contraseña, y nunca un hash sin sal.

    No hay roles ni permisos: todas las cuentas son iguales y ninguna ve los
    datos de otra, así que no hay nada que administrar entre ellas.
    """

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def tiene_password(self) -> bool:
        return bool(self.password_hash)


class Tag(Base):
    """Etiquetas. Deliberadamente COMPARTIDAS entre cuentas.

    La tabla solo guarda nombres; a qué ítem pertenece cada una lo dice
    `media_item_tags`, y los ítems sí son de una cuenta. Así que nadie ve las
    etiquetas de otro en su catálogo. Compartir la fila evita duplicar
    "documental" tantas veces como personas haya en casa.

    El día que haya una nube de etiquetas global habrá que revisarlo: ahí sí se
    vería que alguien usó cierta palabra.
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Lista(Base):
    """Lista/colección del usuario ('para ver con pareja', 'top 2026'...).

    Si `filtro_estado` tiene valor, es una vista automática (p. ej.
    "Completados"): su contenido se calcula en vivo por `MediaItem.status`
    en vez de por la relación `items`, y no admite añadir/quitar a mano ni
    borrarla -- ver `seed_smart_lists()` en routers/lists.py."""

    __tablename__ = "listas"
    # El nombre es único POR CUENTA, no globalmente: que tu pareja tenga una
    # lista "Pendientes" no puede impedirte tener la tuya.
    __table_args__ = (
        UniqueConstraint("usuario_id", "name", name="uq_lista_usuario_nombre"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    filtro_estado: Mapped[str | None] = mapped_column(String(20), nullable=True)

    items: Mapped[list["MediaItem"]] = relationship(
        secondary=list_items, order_by="MediaItem.title", back_populates="listas"
    )


class MediaItem(Base):
    __tablename__ = "media_items"
    __table_args__ = (
        # Todo el catálogo se filtra siempre por cuenta, así que la cuenta va
        # DELANTE en los índices compuestos: un índice que empiece por otra
        # columna no sirve para una consulta que siempre acota por usuario.
        Index("ix_media_items_usuario_estado", "usuario_id", "status"),
        Index("ix_media_items_usuario_tipo_estado", "usuario_id", "media_type", "status"),
        # Cubre el caso más común del catálogo --filtrar por pestaña y estado a
        # la vez-- y sirve también para las consultas que solo filtran por
        # media_type, porque es su primera columna. Ver MC-M1: elegido midiendo
        # con EXPLAIN QUERY PLAN, no a ojo.
        Index("ix_media_items_tipo_estado", "media_type", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # De quién es este ítem. Todas las consultas del catálogo filtran por aquí:
    # dos personas de la misma casa no ven ni tocan lo del otro.
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    media_type: Mapped[MediaType] = mapped_column(SAEnum(MediaType, values_callable=_by_value))
    title: Mapped[str] = mapped_column(String(255))
    # index: se consulta una vez por fila al importar un CSV de IMDb.
    external_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    external_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # tmdb|openlibrary|rawg|imdb|itunes
    cover_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)  # autor / director / estudio
    overview: Mapped[str] = mapped_column(Text, default="")
    cast: Mapped[str | None] = mapped_column(Text, nullable=True)  # reparto principal, separado por coma

    # index: la portada hace ocho consultas filtradas por estado en cada visita.
    status: Mapped[MediaStatus] = mapped_column(
        SAEnum(MediaStatus, values_callable=_by_value), default=MediaStatus.PENDIENTE,
        index=True,
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

    # index: lo filtran la portada y cuatro consultas de estadísticas.
    completed_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)  # fecha de completado (estadísticas)
    genres: Mapped[str | None] = mapped_column(String(255), nullable=True)  # géneros separados por coma

    # Saga/franquicia: nombre editable (manual) + id de colección de TMDB (automático)
    saga: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tmdb_collection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Estrenos: fecha de lanzamiento (pelis/juegos) y si ya se avisó de su salida
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    release_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    tags: Mapped[list["Tag"]] = relationship(secondary=media_item_tags)
    # La relación inversa de Lista.items existe para que SQLAlchemy sepa
    # limpiar `list_items` al borrar un ítem. Declarada solo del lado de Lista,
    # no lo sabía, y cada ítem borrado dejaba una fila muerta: SQLite reasigna
    # los ids de media_items (no hay AUTOINCREMENT), así que un ítem nuevo
    # podía recibir el id de uno borrado y aparecer en las listas del anterior.
    # SQLite tampoco aplica las FK sin PRAGMA foreign_keys=ON, que no se activa.
    listas: Mapped[list["Lista"]] = relationship(
        secondary=list_items, back_populates="items"
    )
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
        order_by="Episode.season_number, Episode.episode_number",
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # index: es el ORDER BY por defecto del catálogo. No evita el recorrido,
    # pero sí el TEMP B-TREE de ordenar el resultado aparte.
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)

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
            # La temporada 0 (especiales/recaps de TMDB) no debe colarse como
            # "próximo": al ordenar por (temporada, episodio) va primero, y
            # contamina el aviso de "próximo episodio" en toda la app.
            if e.season_number != 0 and not e.watched:
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
    # index: lo filtran "Próximamente" de la portada y el job de avisos.
    air_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    runtime_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Si ya se avisó (Telegram) de que este episodio se estrenó
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def code(self) -> str:
        """Código legible tipo S02E05."""
        return "S%02dE%02d" % (self.season_number, self.episode_number)
