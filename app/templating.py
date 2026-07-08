"""Instancia compartida de Jinja2Templates con filtros personalizados (ej. tojson)."""
import json

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["tojson"] = lambda value: json.dumps(value, default=str)
