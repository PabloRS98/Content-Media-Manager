"""Fixtures compartidas: una BD SQLite por test, aislada y desechable."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Reimporta la app apuntando a una BD temporal.

    `app.database` crea el engine en tiempo de import a partir de DB_PATH, así que
    hay que fijar la variable y purgar los módulos antes de importar."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    for nombre in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[nombre]

    import app.database as database
    import app.main as main

    database.init_db()
    return main


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient

    # same-origin: el middleware CSRF debe dejar pasar los POST del propio sitio
    c = TestClient(app_env.app, headers={"sec-fetch-site": "same-origin"})
    c.__dict__["app_module"] = app_env
    return c


@pytest.fixture()
def db(app_env):
    from app.database import SessionLocal

    sesion = SessionLocal()
    yield sesion
    sesion.close()
