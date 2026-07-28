"""Rutas del paquete, resueltas desde la ubicación real del módulo.

Evita depender del directorio de trabajo: `directory="app/templates"` solo
funciona si el proceso arranca desde la raíz del repo."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
