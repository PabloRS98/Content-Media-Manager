"""Tablas de presentación del catálogo, en un solo sitio.

Estaban repartidas entre `routers/catalog.py` (cinco diccionarios en mitad de
una función de 210 líneas) y `templates/_card.html` (el mismo mapeo otra vez,
como un ternario anidado de 13 niveles). Y ya habían divergido: a la copia de
la plantilla le faltaba `wishlist`, así que un ítem deseado se veía como
"Wishlist" en la tarjeta y como "Lo quiero ver" en el desplegable del filtro.

El router ya había arreglado ese mismo bug una vez, y lo dejó comentado:

    WISHLIST tiene que estar en los 5 diccionarios: `statuses` incluye los 5
    valores del enum, y a status_labels.get() le faltaba justo este, así que
    Jinja imprimía literalmente "None" en el desplegable.

Se arregló en el router y la copia de la plantilla siguió con la laguna. De ahí
que esto viva aquí y no en ninguno de los dos.
"""
from .models import MediaItem, MediaStatus, MediaType

# Buckets del filtro de duración: clave, etiqueta y condición, JUNTOS.
#
# Estaban en dos bloques separados por 40 líneas dentro de `list_catalog`: uno
# construía los `query.filter(...)` y otro las etiquetas del desplegable.
# Cambiar "< 150 págs" por "< 200 págs" exigía tocar los dos y nada garantizaba
# que se hiciera -- es el tipo de duplicación que produce bugs de "cambié una
# cosa y la otra se quedó vieja".
#
# La condición es una función sobre la columna y no un rango (min, max) a
# propósito: los límites originales mezclan `<` con `<=` de forma no uniforme, y
# con `hltb_hours`, que es FLOAT, reescribirlos como rangos cerrados cambiaría
# en silencio a qué bucket cae un juego de 10,5 horas.
#
# `columna` es None para las series: ahí se cuenta episodios, así que la
# expresión la construye el router (necesita una subconsulta con la sesión).
BUCKETS_DURACION: dict[MediaType, tuple] = {
    MediaType.LIBRO: (MediaItem.page_count, [
        ("corto", "< 150 págs", lambda c: c < 150),
        ("medio", "150 - 300 págs", lambda c: (c >= 150) & (c <= 300)),
        ("largo", "300 - 500 págs", lambda c: (c > 300) & (c <= 500)),
        ("muy_largo", "> 500 págs", lambda c: c > 500),
    ]),
    MediaType.PELICULA: (MediaItem.runtime_minutes, [
        ("corto", "< 90 mins", lambda c: c < 90),
        ("medio", "90 - 150 mins", lambda c: (c >= 90) & (c <= 150)),
        ("largo", "> 150 mins", lambda c: c > 150),
    ]),
    MediaType.SERIE: (None, [
        ("corto", "< 10 caps", lambda c: c < 10),
        ("medio", "10 - 30 caps", lambda c: (c >= 10) & (c <= 30)),
        ("largo", "> 30 caps", lambda c: c > 30),
    ]),
    MediaType.VIDEOJUEGO: (MediaItem.hltb_hours, [
        ("corto", "< 10 h (HLTB)", lambda c: c < 10),
        ("medio", "10 - 30 h (HLTB)", lambda c: (c >= 10) & (c <= 30)),
        ("largo", "30 - 60 h (HLTB)", lambda c: (c > 30) & (c <= 60)),
        ("muy_largo", "> 60 h (HLTB)", lambda c: c > 60),
    ]),
    MediaType.PODCAST: (MediaItem.runtime_minutes, [
        ("corto", "< 30 mins", lambda c: c < 30),
        ("medio", "30 - 60 mins", lambda c: (c >= 30) & (c <= 60)),
        ("largo", "> 60 mins", lambda c: c > 60),
    ]),
}


def etiquetas_de_duracion(media_type: MediaType | None) -> list[tuple[str, str]]:
    """(clave, etiqueta) de los buckets del tipo, para el desplegable."""
    if media_type not in BUCKETS_DURACION:
        return []
    return [(clave, etiqueta) for clave, etiqueta, _ in BUCKETS_DURACION[media_type][1]]


def condicion_de_duracion(media_type: MediaType | None, clave: str, columna):
    """Condición SQL del bucket `clave`, aplicada sobre `columna`.

    Devuelve None si el tipo no tiene buckets o la clave no existe, para que
    un `?tiempo=loquesea` no filtre nada en vez de reventar.
    """
    if media_type not in BUCKETS_DURACION:
        return None
    for nombre, _, condicion in BUCKETS_DURACION[media_type][1]:
        if nombre == clave:
            return condicion(columna)
    return None

# Etiquetas por tipo de medio: no se "ve" un libro ni se "lee" un videojuego.
# Los cinco estados del enum tienen que estar en las cinco tablas -- lo vigila
# un test parametrizado sobre las 25 combinaciones.
ETIQUETAS_ESTADO: dict[MediaType | None, dict[MediaStatus, str]] = {
    MediaType.LIBRO: {
        MediaStatus.WISHLIST: "Lo quiero",
        MediaStatus.PENDIENTE: "Por leer",
        MediaStatus.EN_PROGRESO: "Leyendo",
        MediaStatus.COMPLETADO: "Leído",
        MediaStatus.ABANDONADO: "Abandonado",
    },
    MediaType.PELICULA: {
        MediaStatus.WISHLIST: "Lo quiero ver",
        MediaStatus.PENDIENTE: "Por ver",
        MediaStatus.EN_PROGRESO: "Viendo",
        MediaStatus.COMPLETADO: "Visto",
        MediaStatus.ABANDONADO: "Abandonado",
    },
    MediaType.SERIE: {
        MediaStatus.WISHLIST: "Lo quiero ver",
        MediaStatus.PENDIENTE: "Por ver",
        MediaStatus.EN_PROGRESO: "Viendo",
        MediaStatus.COMPLETADO: "Visto",
        MediaStatus.ABANDONADO: "Abandonado",
    },
    MediaType.VIDEOJUEGO: {
        MediaStatus.WISHLIST: "Lo quiero jugar",
        MediaStatus.PENDIENTE: "Por jugar",
        MediaStatus.EN_PROGRESO: "Jugando",
        MediaStatus.COMPLETADO: "Terminado/Jugado",
        MediaStatus.ABANDONADO: "Abandonado",
    },
    MediaType.PODCAST: {
        MediaStatus.WISHLIST: "Lo quiero escuchar",
        MediaStatus.PENDIENTE: "Por escuchar",
        MediaStatus.EN_PROGRESO: "Escuchando",
        MediaStatus.COMPLETADO: "Escuchado",
        MediaStatus.ABANDONADO: "Abandonado",
    },
    # Sin tipo (la pestaña "todo" del catálogo): términos neutros.
    None: {
        MediaStatus.WISHLIST: "Wishlist",
        MediaStatus.PENDIENTE: "Pendiente",
        MediaStatus.EN_PROGRESO: "En progreso",
        MediaStatus.COMPLETADO: "Completado",
        MediaStatus.ABANDONADO: "Abandonado",
    },
}


def etiquetas_de(media_type: MediaType | None) -> dict[MediaStatus, str]:
    """Las cinco etiquetas del tipo pedido, o las neutras si no hay tipo."""
    return ETIQUETAS_ESTADO.get(media_type, ETIQUETAS_ESTADO[None])


def etiqueta_estado(item) -> str:
    """Etiqueta de un ítem concreto, para las tarjetas.

    Se expone como global de Jinja en `templating.py`, igual que `build_qs`,
    para que la plantilla no vuelva a tener su propia copia del mapeo.
    """
    return etiquetas_de(item.media_type)[item.status]
