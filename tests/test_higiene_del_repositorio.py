"""El repositorio no debe arrastrar entornos, cachés ni bases de datos sueltas.

El criterio de aceptación real del hallazgo es "`git status --porcelain` sale
vacío en un clon recién montado", que no se puede comprobar desde aquí sin
clonar. Lo que sí se puede fijar es que los patrones que lo garantizan sigan en
los dos ficheros de exclusión: son los que se quedaron cortos.
"""
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# `.venv/` a secas no cubría `.venv-audit/`, que es como llegaron a la raíz
# miles de ficheros y binarios compilados sin que el .gitignore dijera nada.
PATRONES = [".venv*/", "__pycache__/", ".pytest_cache/", ".ruff_cache/",
            "*.db", "*.db-wal", "*.db-shm", ".env"]


@pytest.mark.parametrize("patron", PATRONES)
def test_el_gitignore_cubre_los_artefactos(patron):
    assert patron in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("patron", PATRONES)
def test_el_dockerignore_cubre_los_artefactos(patron):
    """En paralelo con el .gitignore: lo que no está aquí se sube al daemon en
    cada build aunque el Dockerfile no lo copie."""
    assert patron in (RAIZ / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_el_env_example_sigue_versionandose():
    """`.env.*` también tapaba la plantilla, que es lo primero que necesita un
    clon nuevo."""
    assert "!.env.example" in (RAIZ / ".gitignore").read_text(encoding="utf-8")
    assert (RAIZ / ".env.example").exists()


def test_no_hay_bases_de_datos_sueltas_en_la_raiz():
    """La auditoría encontró un .db de fixtures olvidado junto al código."""
    assert list(RAIZ.glob("*.db")) == []
