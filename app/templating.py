"""Instancia compartida de Jinja2Templates.

No se sobreescribe el filtro `tojson`: el que trae Jinja (`htmlsafe_json_dumps`)
escapa `<`, `>`, `&` y `'` como `\\uXXXX` para poder incrustar JSON dentro de un
`<script>` sin que se pueda cerrar la etiqueta. Un `json.dumps` crudo aquí no
escapa nada, y stats.html mete datos controlados por el usuario (géneros de
`MediaItem.genres`, editable y rellenado desde CSVs de importación) dentro de
un `<script>` vía `| tojson | safe` -- exactamente el vector que el tojson de
Jinja existe para cerrar."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
