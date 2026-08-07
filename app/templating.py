"""Instancia compartida de Jinja2Templates.

No se sobreescribe el filtro `tojson`: el que trae Jinja (`htmlsafe_json_dumps`)
escapa `<`, `>`, `&` y `'` como `\\uXXXX` para poder incrustar JSON dentro de un
`<script>` sin que se pueda cerrar la etiqueta. Un `json.dumps` crudo aquí no
escapa nada, y stats.html mete datos controlados por el usuario (géneros de
`MediaItem.genres`, editable y rellenado desde CSVs de importación) dentro de
un `<script>` vía `| tojson | safe` -- exactamente el vector que el tojson de
Jinja existe para cerrar."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

from .catalogo_config import etiqueta_estado

# Ruta absoluta (relativa a este fichero), no "app/templates": una ruta
# relativa al cwd solo funciona si el proceso arranca desde la raíz del repo.
# En Docker el WORKDIR /app lo salva, pero rompe cualquier otro modo de arranque.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# StrictUndefined: una variable ausente lanza error en vez de renderizar vacío.
# Con el comportamiento por defecto, referenciar algo que el router no pasa es
# `Undefined`, que es falsy, así que un `{% if %}` sobre ella falla en silencio
# y el bloque simplemente no se pinta: un fallo así puede pasar meses sin verse.
#
# Es exactamente el tipo de bug que ya ocurrió aquí con `status_labels.get()`
# devolviendo None. El patrón `{% if x is defined and x %}` sigue funcionando,
# que es la forma correcta de preguntar por algo opcional.
templates.env.undefined = StrictUndefined

_FILTER_PARAMS = ("tipo", "estado", "genero", "tiempo", "orden")


def build_qs(request: Request, **overrides: str | int | None) -> str:
    """Query string del catálogo aplicando `overrides` sobre los filtros ya
    activos en `request`, para que un botón de filtro cambie un solo
    parámetro sin descartar los demás.

    `pagina` NO está en `_FILTER_PARAMS` a propósito: no se arrastra de la
    petición, así que un botón de filtro siempre devuelve a la página 1 --
    cambiar de filtro invalida la paginación anterior. Los enlaces de
    "Anterior"/"Siguiente" sí la pasan, como `override` explícito
    (`build_qs(request, pagina=pagina + 1)`), que es el único caso en el que
    tiene sentido conservarla.

    Todo sale por `urlencode`. Los enlaces de paginación se construían antes
    concatenando cadenas a mano, y un género como "Sci-Fi & Fantasy" (real en
    TMDB) partía la URL en dos parámetros: el filtro se perdía justo al pasar
    de página."""
    params = {k: v for k, v in request.query_params.items() if k in _FILTER_PARAMS and v}
    for key, value in overrides.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)
    return "?" + urlencode(params) if params else ""


templates.env.globals["build_qs"] = build_qs

# La etiqueta de estado depende del tipo de medio (no se "ve" un libro ni se
# "lee" un videojuego). Se expone como global para que las plantillas no
# vuelvan a tener su propia copia del mapeo, que es como acabó divergiendo del
# router. Ver `catalogo_config.py`.
templates.env.globals["etiqueta_estado"] = etiqueta_estado


def minutos_estimados(item) -> int | None:
    """Minutos que se estima que lleva consumir un ítem.

    Se expone para que las plantillas no repitan la fórmula: `detail.html`
    tenía escrito el factor 1.5 a mano, duplicado con
    `metadata.MINUTES_PER_PAGE`, y cambiar uno dejaba el otro desincronizado.
    """
    from .services import metadata

    return metadata.estimated_minutes(item)


templates.env.globals["minutos_estimados"] = minutos_estimados
