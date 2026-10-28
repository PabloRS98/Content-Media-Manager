"""El CI es la puerta de la imagen publicada: estas garantías no pueden
deshacerse sin que algo falle (tercera auditoría, bloque 4)."""
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"


def _ci() -> dict:
    return yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))


def test_la_imagen_solo_se_publica_tras_todo_el_ci():
    """R3-A5: antes la publicación solo esperaba a una copia de los tests."""
    trabajos = _ci()["jobs"]
    publicar = trabajos.pop("publicar")
    assert set(publicar["needs"]) == set(trabajos), (
        "publicar debe esperar a todos los demás jobs: %s" % sorted(trabajos)
    )
    assert publicar["if"] == "github.event_name == 'push'"


def test_ningun_otro_workflow_publica_imagenes():
    """Un segundo workflow con `packages: write` se saltaría la puerta."""
    for wf in WORKFLOWS.glob("*.y*ml"):
        if wf.name != "ci.yml":
            assert "packages" not in wf.read_text(encoding="utf-8"), wf.name


def test_los_tags_de_version_disparan_el_ci():
    """R3-M22: sin esto, empujar v1.2.3 no publicaría `:1.2.3`."""
    disparadores = _ci()[True]  # YAML 1.1 lee la clave `on:` como True
    assert "v*.*.*" in disparadores["push"]["tags"]


def test_permisos_minimos_por_defecto():
    """R3-M23."""
    ci = _ci()
    assert ci["permissions"] == {"contents": "read"}
    for nombre, job in ci["jobs"].items():
        if nombre != "publicar":
            assert "permissions" not in job, nombre


def test_las_acciones_van_fijadas_por_sha():
    """R3-M23: un tag (`@v4`) lo puede mover quien controle la acción."""
    for wf in WORKFLOWS.glob("*.y*ml"):
        for linea in wf.read_text(encoding="utf-8").splitlines():
            m = re.search(r"uses:\s*(\S+)", linea)
            if m and not m.group(1).startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", m.group(1)), (
                    "%s: %s" % (wf.name, linea.strip())
                )


def test_contributing_no_manda_usar_ensure_columns():
    """R3-M24: el mecanismo es Alembic desde MC-M4."""
    texto = (RAIZ / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "Schema changes go through `ensure_columns()`" not in texto
    assert "alembic revision --autogenerate" in texto
