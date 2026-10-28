"""Importadores de CSV: IMDb, Goodreads/StoryGraph (libros), Backloggd/genérico
(juegos), Letterboxd (películas) y Trakt (películas y series). [N6]

Tolerantes con los nombres de columna (cada export usa los suyos) y con el
separador (`;` si viene de un Excel en español).

**Duplicados.** Cada fila se busca entre lo que ya hay del mismo tipo en la
cuenta (y entre las filas anteriores del mismo fichero) con `Indice`: primero
por id externo canónico y, si no, por título normalizado y año. Antes la clave
era solo el título, así que "Dune" (1984) y "Dune" (2021) quedaban en una
[R3-M14]; y el mismo título de IMDb (`imdb:tt…`) y de Trakt (`tt…`) no se
reconocía [R3-M15].

**Un duplicado completa al existente** en vez de descartarse: lo que el
existente no tenga (nota, año, fecha de terminado…) se rellena, y el estado
solo avanza (de "por ver" a "visto", nunca al revés). Importar `ratings.csv`
de Letterboxd después de `watched.csv` traía cero notas, porque todas las
filas eran "duplicados". [R3-M16]
"""
import csv
import io
import logging
import re
import unicodedata
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from .. import fechas, numeros
from ..models import MediaItem, MediaStatus, MediaType
from . import generos, metadata

logger = logging.getLogger(__name__)


class CsvIlegible(ValueError):
    """El fichero no se puede leer como CSV (campo enorme, comillas rotas...).

    Antes salía como un 500. Se importa todo o nada: el router no hace commit.
    [R3-B2]
    """


def lector(text: str) -> csv.DictReader:
    """DictReader con el separador que use el fichero.

    Excel en español guarda los CSV con `;`. Con `,` fijo, cada fila era una
    sola columna sin "Title" y el fichero entero salía como "omitidos" sin
    explicación. Se decide por la cabecera, que es lo único seguro de mirar:
    en las filas, una coma dentro de un título no dice nada. [R3-B13]
    """
    cabecera = text.lstrip("﻿").split("\n", 1)[0]
    separador = max((",", ";", "\t"), key=cabecera.count)
    return csv.DictReader(io.StringIO(text), delimiter=separador)


def filas(reader: csv.DictReader):
    try:
        yield from reader
    except csv.Error as e:
        raise CsvIlegible("línea %d: %s" % (reader.line_num, e)) from e


def _get(row: dict, *names: str) -> str:
    """Primer valor no vacío entre las columnas candidatas (case-insensitive).

    "No vacío" y no "presente": un CSV bilingüe trae "Title" y "Título", y una
    columna presente pero vacía no puede tapar a la alternativa con datos.
    Había otra copia en el importador de IMDb que hacía justo eso. [R3-B16]
    """
    lowered = {(k or "").strip().lower(): (v or "").strip()
               for k, v in row.items() if isinstance(v, str) or v is None}
    for n in names:
        v = lowered.get(n.strip().lower())
        if v:
            return v
    return ""


def _int(value: str, rango=None):
    return numeros.entero(value, rango)


def _rating5_to_10(value: str):
    """Convierte una nota en escala 0-5 (con medias) a 1-10; 0/vacío -> None."""
    r = numeros.decimal(value)
    if r is None or r <= 0:
        return None
    return max(1, min(10, round(r * 2)))


_FORMATOS_DE_FECHA = ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y")
_FECHA_EN_TEXTO = re.compile(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}")


def _una_fecha(value: str) -> date | None:
    value = value.strip()
    # Trakt y otros dan "2024-03-15T21:04:00.000Z": basta con el día.
    if len(value) > 10 and value[4] == "-" and value[10] in "T ":
        value = value[:10]
    for fmt in _FORMATOS_DE_FECHA:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _rango_de_fechas(value: str) -> tuple[date | None, date | None]:
    """(comienzo, fin) de un campo que puede traer varias fechas.

    StoryGraph da "Dates Read" como `2022/01/05-2022/02/10` (y varias
    lecturas separadas por comas). Antes se cortaba por la primera `/` y se
    quedaba en "2022", es decir, el 1 de enero. [R3-M17]
    """
    if not value:
        return None, None
    encontradas = _FECHA_EN_TEXTO.findall(value)
    if len(encontradas) > 1:
        return _una_fecha(encontradas[0]), _una_fecha(encontradas[-1])
    unica = _una_fecha(value)
    return None, unica


def _parse_date(value: str) -> date | None:
    """La fecha de un campo; si trae un rango, la del final."""
    return _rango_de_fechas(value or "")[1]


def normalizar_titulo(titulo: str) -> str:
    """"Amélie", "AMELIE" y "Amelie." son el mismo título."""
    sin_tildes = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^\w]+", " ", sin_tildes.lower()).split())


_IMDB = re.compile(r"^(?:imdb:)?(tt\d+)$", re.IGNORECASE)


def id_canonico(external_id: str | None) -> str | None:
    """Una sola forma de escribir cada id externo. [R3-M15]

    IMDb se guarda siempre como `imdb:tt…`, que es lo que espera el
    enriquecimiento de portadas (`enrich.py` busca ese prefijo): los `tt…` a
    secas de Trakt no se enriquecían y no se reconocían como duplicados.
    """
    if not external_id:
        return None
    external_id = external_id.strip()
    m = _IMDB.match(external_id)
    if m:
        return "imdb:" + m.group(1).lower()
    return external_id


def _clave_de_id(external_id: str | None, fuente: str | None) -> str | None:
    """Clave para comparar ids: el número 620 de Steam y el 620 de RAWG son
    juegos distintos, así que fuera de IMDb (cuyo `tt…` es inconfundible) la
    fuente forma parte de la clave."""
    eid = id_canonico(external_id)
    if not eid:
        return None
    if eid.startswith("imdb:"):
        return eid
    return "%s:%s" % (fuente or "", eid)


class Indice:
    """Lo que ya hay de un tipo en una cuenta, para reconocer duplicados.

    Un título con año distinto no es el mismo (los remakes existen); si a una
    de las dos partes le falta el año, sí se da por el mismo, porque crear un
    duplicado es peor que completar uno. Con `con_autor` (libros), dos autores
    distintos tampoco son el mismo libro. Dos ids externos de la misma fuente
    que no coinciden, tampoco.
    """

    def __init__(self, db: Session, media_type: MediaType, usuario_id: int, con_autor=False):
        self.con_autor = con_autor
        self._por_id: dict[str, MediaItem] = {}
        self._por_titulo: dict[str, list[MediaItem]] = {}
        for item in db.query(MediaItem).filter(
            MediaItem.media_type == media_type,
            MediaItem.usuario_id == usuario_id,
        ):
            self.añadir(item)

    def añadir(self, item: MediaItem) -> None:
        eid = _clave_de_id(item.external_id, item.external_source)
        if eid:
            self._por_id.setdefault(eid, item)
        self._por_titulo.setdefault(normalizar_titulo(item.title or ""), []).append(item)

    def buscar(self, titulo: str, año: int | None = None, creador: str | None = None,
               external_id: str | None = None, fuente: str | None = None) -> MediaItem | None:
        eid = _clave_de_id(external_id, fuente)
        if eid and eid in self._por_id:
            return self._por_id[eid]
        for cand in self._por_titulo.get(normalizar_titulo(titulo), []):
            if año and cand.year and año != cand.year:
                continue
            if (self.con_autor and creador and cand.creator
                    and normalizar_titulo(creador) != normalizar_titulo(cand.creator)):
                continue
            # Mismo título pero otro id de la misma fuente: es otra obra.
            cand_eid = _clave_de_id(cand.external_id, cand.external_source)
            if eid and cand_eid and eid.rsplit(":", 1)[0] == cand_eid.rsplit(":", 1)[0]:
                continue
            return cand
        return None


_AVANCE = {
    MediaStatus.WISHLIST: 0,
    MediaStatus.PENDIENTE: 1,
    MediaStatus.EN_PROGRESO: 2,
    MediaStatus.COMPLETADO: 3,
}


def completar(item: MediaItem, *, status: MediaStatus | None = None,
              completed_at: date | None = None, started_at: date | None = None,
              **campos) -> bool:
    """Rellena en `item` lo que le falte con lo que trae la fila. True si cambió algo.

    Nunca pisa un dato que ya esté: lo que escribiste a mano gana a lo que
    diga un export. El estado solo avanza, y "abandonado" no se toca.
    """
    cambiado = False
    for campo, valor in campos.items():
        if valor in (None, "") or getattr(item, campo) not in (None, ""):
            continue
        setattr(item, campo, valor)
        cambiado = True
    if (status is not None and item.status in _AVANCE and status in _AVANCE
            and _AVANCE[status] > _AVANCE[item.status]):
        metadata.cambiar_estado(item, status)
        if status == MediaStatus.COMPLETADO and completed_at:
            item.completed_at = completed_at
        cambiado = True
    elif item.status == MediaStatus.COMPLETADO and completed_at and item.completed_at is None:
        item.completed_at = completed_at
        cambiado = True
    if started_at and item.started_at is None and item.status != MediaStatus.WISHLIST:
        item.started_at = started_at
        cambiado = True
    return cambiado


class _Recuento:
    def __init__(self):
        self.creados = self.duplicados = self.actualizados = self.omitidos = 0

    def duplicado(self, cambiado: bool) -> None:
        self.duplicados += 1
        if cambiado:
            self.actualizados += 1

    def como_dict(self) -> dict:
        return {"creados": self.creados, "duplicados": self.duplicados,
                "actualizados": self.actualizados, "omitidos": self.omitidos}


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


def _nuevo(db: Session, indice: Indice, recuento: _Recuento, **campos) -> MediaItem:
    item = MediaItem(**campos)
    db.add(item)
    indice.añadir(item)
    recuento.creados += 1
    # Un flush cada 500 filas para no acumular 5 000 objetos en la sesión
    # antes del único commit final. No abre transacción propia: si algo falla
    # después, el rollback sigue deshaciendo el fichero entero.
    if recuento.creados % 500 == 0:
        db.flush()
    return item


def import_books_csv(db: Session, text: str, usuario_id: int) -> dict:
    reader = lector(text)
    fields = " ".join(reader.fieldnames or []).lower()
    source = "storygraph" if "read status" in fields else "goodreads"
    indice = Indice(db, MediaType.LIBRO, usuario_id, con_autor=True)
    r = _Recuento()

    for row in filas(reader):
        title = _get(row, "Title")
        if not title:
            r.omitidos += 1
            continue
        author = _get(row, "Author", "Authors", "Primary Author") or None
        year = _int(_get(row, "Original Publication Year", "Year Published", "Year"), numeros.AÑO)
        status = _BOOK_STATUS.get(_get(row, "Exclusive Shelf", "Read Status", "Status").lower(),
                                  MediaStatus.PENDIENTE)
        comienzo, fin = _rango_de_fechas(_get(row, "Date Read", "Last Date Read", "Dates Read"))
        if status == MediaStatus.COMPLETADO and fin is None:
            # Goodreads deja "Date Read" vacío en lo leído antes de usarlo, y
            # sin fecha un libro completado no sale en ningún año: ni en las
            # estadísticas ni en el diario. La fecha en que se añadió es la
            # mejor cota que hay. [R3-M17]
            fin = _parse_date(_get(row, "Date Added"))
        datos = {
            "rating": _rating5_to_10(_get(row, "My Rating", "Star Rating", "Rating")),
            "page_count": _int(_get(row, "Number of Pages", "Pages", "Page Count"), numeros.PAGINAS),
            "year": year,
            "creator": author,
        }
        completado = fin if status == MediaStatus.COMPLETADO else None

        existente = indice.buscar(title, year, author)
        if existente:
            r.duplicado(completar(existente, status=status, completed_at=completado,
                                  started_at=comienzo, **datos))
            continue
        _nuevo(db, indice, r,
               usuario_id=usuario_id, media_type=MediaType.LIBRO, title=title,
               external_source=source, status=status, completed_at=completado,
               started_at=comienzo if status in (MediaStatus.COMPLETADO, MediaStatus.EN_PROGRESO) else None,
               **datos)
    db.commit()
    return r.como_dict()


def import_letterboxd_csv(db: Session, text: str, usuario_id: int,
                          pendientes: bool = False) -> dict:
    """Películas exportadas de Letterboxd.

    `pendientes` lo dice quien importa, y no se puede deducir: el CSV de la
    watchlist tiene EXACTAMENTE las mismas columnas que el de vistas (fecha,
    nombre, año, enlace). Adivinarlo por el nombre del fichero sería adivinar.
    """
    reader = lector(text)
    indice = Indice(db, MediaType.PELICULA, usuario_id)
    estado = MediaStatus.PENDIENTE if pendientes else MediaStatus.COMPLETADO
    r = _Recuento()

    for row in filas(reader):
        title = _get(row, "Name", "Title", "Film")
        if not title:
            r.omitidos += 1
            continue
        year = _int(_get(row, "Year"), numeros.AÑO)
        # "Watched Date" antes que "Date": en diary.csv, "Date" es el día en
        # que se escribió la entrada, no el día en que se vio. [R3-B12]
        fecha = _parse_date(_get(row, "Watched Date", "Date"))
        completado = fecha if estado == MediaStatus.COMPLETADO else None
        # Letterboxd puntúa sobre 5 con medias estrellas; aquí es sobre 10.
        rating = _rating5_to_10(_get(row, "Rating"))

        existente = indice.buscar(title, year)
        if existente:
            r.duplicado(completar(existente, status=estado, completed_at=completado,
                                  rating=rating, year=year))
            continue
        _nuevo(db, indice, r,
               usuario_id=usuario_id, media_type=MediaType.PELICULA, title=title,
               external_source="letterboxd", rating=rating, year=year,
               status=estado, completed_at=completado)
    db.commit()
    return r.como_dict()


def import_trakt_csv(db: Session, text: str, usuario_id: int) -> dict:
    """Historial de Trakt: películas y series en el mismo fichero.

    Los episodios NO se convierten en ítems: veinte filas de la misma serie son
    una serie, no veinte entradas. Lo que se guarda de ellos es hasta dónde
    llegaste, en las notas, porque los episodios de verdad los trae TMDB al
    enriquecer y hasta entonces no hay a qué marcarlo. Decirlo en las notas es
    honesto; inventar episodios vacíos para marcarlos, no.
    """
    reader = lector(text)
    pelis = Indice(db, MediaType.PELICULA, usuario_id)
    series_existentes = Indice(db, MediaType.SERIE, usuario_id)

    # Las series se agrupan antes de crear nada: cada fila es un episodio.
    series: dict[str, dict] = {}
    r = _Recuento()

    for row in filas(reader):
        title = _get(row, "title", "show", "show_title", "Title")
        if not title:
            r.omitidos += 1
            continue

        tipo = _get(row, "type", "media_type").lower()
        temporada = _int(_get(row, "season"))
        episodio = _int(_get(row, "episode", "number"))
        es_serie = tipo in ("episode", "show", "season") or (
            not tipo and temporada is not None
        )
        imdb = id_canonico(_get(row, "imdb_id", "imdb"))
        year = _int(_get(row, "year"), numeros.AÑO)
        fecha = _parse_date(_get(row, "watched_at", "date"))

        if es_serie:
            datos = series.setdefault(normalizar_titulo(title), {
                "title": title, "year": year, "imdb": imdb,
                "ultimo": None, "fecha": None,
            })
            if temporada is not None and episodio is not None:
                marca = (temporada, episodio)
                if datos["ultimo"] is None or marca > datos["ultimo"]:
                    datos["ultimo"] = marca
            if fecha and (datos["fecha"] is None or fecha < datos["fecha"]):
                datos["fecha"] = fecha  # la más antigua: cuándo empezaste
            continue

        existente = pelis.buscar(title, year, external_id=imdb)
        if existente:
            r.duplicado(completar(existente, status=MediaStatus.COMPLETADO, completed_at=fecha,
                                  year=year, external_id=imdb))
            continue
        _nuevo(db, pelis, r,
               usuario_id=usuario_id, media_type=MediaType.PELICULA, title=title,
               year=year, external_id=imdb, external_source="imdb" if imdb else "trakt",
               status=MediaStatus.COMPLETADO, completed_at=fecha)

    for datos in series.values():
        existente = series_existentes.buscar(datos["title"], datos["year"], external_id=datos["imdb"])
        if existente:
            r.duplicado(completar(existente, status=MediaStatus.EN_PROGRESO, started_at=datos["fecha"],
                                  year=datos["year"], external_id=datos["imdb"]))
            continue
        notas = ["Importado de Trakt."]
        if datos["ultimo"]:
            notas.append("Visto hasta S%02dE%02d." % datos["ultimo"])
        _nuevo(db, series_existentes, r,
               usuario_id=usuario_id, media_type=MediaType.SERIE, title=datos["title"],
               year=datos["year"], external_id=datos["imdb"],
               external_source="imdb" if datos["imdb"] else "trakt",
               status=MediaStatus.EN_PROGRESO, started_at=datos["fecha"],
               notes=" ".join(notas))

    db.commit()
    return r.como_dict()


def import_games_csv(db: Session, text: str, usuario_id: int) -> dict:
    reader = lector(text)
    indice = Indice(db, MediaType.VIDEOJUEGO, usuario_id)
    r = _Recuento()

    for row in filas(reader):
        title = _get(row, "Title", "Name", "Game")
        if not title:
            r.omitidos += 1
            continue
        status = _GAME_STATUS.get(_get(row, "Status", "Shelf").lower(), MediaStatus.PENDIENTE)
        fecha = _parse_date(_get(row, "Date", "Completed", "Date Completed"))
        completado = fecha if status == MediaStatus.COMPLETADO else None
        year = _int(_get(row, "Release Date", "Year", "Released"), numeros.AÑO)
        datos = {
            "creator": _get(row, "Developer", "Studio", "Platforms", "Platform") or None,
            "rating": _rating5_to_10(_get(row, "Rating", "Score", "My Rating")),
            "hltb_hours": numeros.decimal(_get(row, "Hours", "Playtime", "Time"), numeros.HORAS),
            "year": year,
        }

        existente = indice.buscar(title, year)
        if existente:
            r.duplicado(completar(existente, status=status, completed_at=completado, **datos))
            continue
        _nuevo(db, indice, r,
               usuario_id=usuario_id, media_type=MediaType.VIDEOJUEGO, title=title,
               external_source="import", status=status, completed_at=completado, **datos)
    db.commit()
    return r.como_dict()


# IMDb usa sus propios "Title Type"; se mapean a nuestras categorías.
# En minúsculas para búsquedas tolerantes a mayúsculas/minúsculas.
TITLE_TYPE_MAP = {
    "movie": MediaType.PELICULA,
    "tvmovie": MediaType.PELICULA,
    "short": MediaType.PELICULA,
    "tvshort": MediaType.PELICULA,
    "tvspecial": MediaType.PELICULA,
    "video": MediaType.PELICULA,
    "película": MediaType.PELICULA,
    "pelicula": MediaType.PELICULA,
    "película de televisión": MediaType.PELICULA,
    "pelicula de television": MediaType.PELICULA,
    "cortometraje": MediaType.PELICULA,
    "cortometraje de televisión": MediaType.PELICULA,
    "cortometraje de television": MediaType.PELICULA,
    "especial de televisión": MediaType.PELICULA,
    "especial de television": MediaType.PELICULA,

    "tvseries": MediaType.SERIE,
    "tvminiseries": MediaType.SERIE,
    "tvpilot": MediaType.SERIE,
    "serie de televisión": MediaType.SERIE,
    "serie de television": MediaType.SERIE,
    "miniserie de televisión": MediaType.SERIE,
    "miniserie de television": MediaType.SERIE,
    "serie": MediaType.SERIE,
    "miniserie": MediaType.SERIE,

    "videogame": MediaType.VIDEOJUEGO,
    "videojuego": MediaType.VIDEOJUEGO,
}


def import_imdb_csv(db: Session, text: str, usuario_id: int) -> dict:
    """Export de "Your Ratings" o de la Watchlist de IMDb.

    Una fila con nota es algo visto; sin nota, algo pendiente. Los índices de
    duplicados se cargan una vez (un SELECT por tipo) y absorben las filas
    del propio fichero: con autoflush=False, los `db.add()` anteriores no
    estarían en la base aunque se consultara, y un CSV con la misma película
    en "Ratings" y "Watchlist" la habría creado dos veces.
    """
    reader = lector(text)
    lowered_fields = {(f or "").strip().lower() for f in reader.fieldnames or []}
    tiene_rating = any(c in lowered_fields for c in
                       ("your rating", "tu calificación", "tu calificacion", "rating"))
    indices: dict[MediaType, Indice] = {}
    r = _Recuento()

    for row in filas(reader):
        title_type = _get(row, "Title Type", "Tipo de título", "Tipo de titulo", "Type").lower()
        media_type = TITLE_TYPE_MAP.get(title_type)
        title = _get(row, "Title", "Título", "Titulo")
        if media_type is None or not title:
            r.omitidos += 1
            continue
        if media_type not in indices:
            indices[media_type] = Indice(db, media_type, usuario_id)
        indice = indices[media_type]

        external_id = id_canonico(_get(row, "Const", "ID", "id", "Constante"))
        year = _int(_get(row, "Year", "Año", "Ano"), numeros.AÑO)
        directors = _get(row, "Directors", "Directores", "Director") or None
        rating = numeros.nota(_get(row, "Your Rating", "Tu calificación", "Tu calificacion", "Rating")) \
            if tiene_rating else None
        fecha = _parse_date(_get(row, "Date Rated", "Fecha de calificación",
                                 "Fecha de calificacion", "Date"))
        status = MediaStatus.COMPLETADO if rating is not None else MediaStatus.PENDIENTE
        completado = (fecha or fechas.hoy()) if status == MediaStatus.COMPLETADO else None

        existente = indice.buscar(title, year, external_id=external_id, fuente="imdb")
        if existente:
            r.duplicado(completar(existente, status=status, completed_at=completado,
                                  rating=rating, year=year, creator=directors,
                                  external_id=external_id))
            continue

        notas = ["Importado de IMDB."]
        imdb_rating = _get(row, "IMDb Rating", "Calificación de IMDb", "Calificacion de IMDb", "IMDB Rating")
        num_votes = _get(row, "Num Votes", "Número de votos", "Numero de votos")
        if imdb_rating:
            sufijo_votos = f" ({num_votes} votos)" if num_votes else ""
            notas.append(f"Rating IMDb: {imdb_rating}{sufijo_votos}.")
        cuando = datetime.combine(fecha, time()) if fecha else datetime.now()
        nuevo = _nuevo(db, indice, r,
                       usuario_id=usuario_id, media_type=media_type, title=title,
                       external_id=external_id, external_source="imdb", year=year,
                       creator=directors, overview="", status=status, rating=rating,
                       notes=" ".join(notas), completed_at=completado,
                       created_at=cuando, updated_at=cuando)
        # Los géneros son una relación desde [N4]: se asignan tras crear el
        # ítem, no como argumento del constructor.
        generos.asignar(db, nuevo, _get(row, "Genres", "Géneros", "Generos"))

    db.commit()
    return r.como_dict()
