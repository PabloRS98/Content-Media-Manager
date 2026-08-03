"""Instancia compartida de Jinja2Templates con filtros personalizados (ej. tojson)."""
import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

# Ruta absoluta (relativa a este fichero), no "app/templates": una ruta
# relativa al cwd solo funciona si el proceso arranca desde la raíz del repo.
# En Docker el WORKDIR /app lo salva, pero rompe cualquier otro modo de arranque.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["tojson"] = lambda value: json.dumps(value, default=str)
