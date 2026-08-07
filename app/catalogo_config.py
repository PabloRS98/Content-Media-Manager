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
from .models import MediaStatus, MediaType

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
