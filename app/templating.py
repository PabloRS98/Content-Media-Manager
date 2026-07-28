"""Instancia compartida de Jinja2Templates.

NO sobreescribir aquí el filtro `tojson`: el de Jinja2 (`htmlsafe_json_dumps`)
escapa `<`, `>`, `&` y `'` como secuencias \\uXXXX, que es justo lo que permite
incrustar datos dentro de un bloque <script> sin que se pueda cerrar la etiqueta.
Un `json.dumps` crudo en su lugar abre un XSS almacenado (ver stats.html).

Para serializar tipos que json no conoce (date/datetime) se ajusta la política
`json.dumps_kwargs`, que Jinja pasa a su propio serializador seguro."""
from fastapi.templating import Jinja2Templates

from .paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "default": str}
