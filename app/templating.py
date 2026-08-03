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

# Ruta absoluta (relativa a este fichero), no "app/templates": una ruta
# relativa al cwd solo funciona si el proceso arranca desde la raíz del repo.
# En Docker el WORKDIR /app lo salva, pero rompe cualquier otro modo de arranque.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

_FILTER_PARAMS = ("tipo", "estado", "genero", "tiempo", "orden")


def build_qs(request: Request, **overrides: str | None) -> str:
    """Query string del catálogo aplicando `overrides` sobre los filtros ya
    activos en `request`, para que un botón de filtro cambie un solo
    parámetro sin descartar los demás (ni la página, que siempre vuelve a 1
    porque cambiar de filtro invalida la paginación anterior)."""
    params = {k: v for k, v in request.query_params.items() if k in _FILTER_PARAMS and v}
    for key, value in overrides.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)
    return "?" + urlencode(params) if params else ""


templates.env.globals["build_qs"] = build_qs
