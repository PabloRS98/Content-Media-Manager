"""Configuración común de los tests.

Dos decisiones importantes:

1. Las variables de entorno se fijan ANTES de importar nada de `app`, porque
   `app.config.Settings` y el motor de SQLAlchemy se construyen al importar el
   módulo. Si se hiciera después, los tests escribirían en la base de datos real.

2. Los tests no salen a la red. Un fixture autouse corta `httpx` de raíz: si
   algún día un test empieza a llamar a TMDB o a Google Books, falla en vez de
   volverse lento e intermitente según el día.
"""
import os
import pathlib
import tempfile

_TMP = tempfile.mkdtemp(prefix="media-catalog-tests-")
os.environ["DB_PATH"] = str(pathlib.Path(_TMP) / "no-usar.db")
os.environ["ENABLE_AUTH"] = "false"
# Sin claves: ningún servicio externo se considera configurado
for _clave in ("TMDB_API_KEY", "RAWG_API_KEY", "GOOGLE_BOOKS_API_KEY",
               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ[_clave] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Episode, Lista, MediaItem, MediaStatus, MediaType  # noqa: E402


class RedProhibida(RuntimeError):
    """Se lanza si un test intenta salir a internet."""


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Corta cualquier petición HTTP que salga de verdad a internet.

    Se parchea el transporte (`HTTPTransport`), no `Client.send`: el propio
    TestClient es un `httpx.Client` con un transporte en memoria, así que
    bloquear `send` dejaría también sin efecto las peticiones a nuestra app.
    """
    def _bloquear(self, *args, **kwargs):
        raise RedProhibida(
            "Un test ha intentado salir a la red. Usa un doble o stubea el servicio."
        )
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _bloquear)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _bloquear)


@pytest.fixture
def db(tmp_path):
    """Sesión contra una base de datos SQLite nueva por test.

    Se crea el esquema desde los modelos (no con `ensure_columns`): los modelos
    son la fuente de verdad y así el test no depende del historial de migraciones.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Sesion = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    sesion = Sesion()
    try:
        yield sesion
    finally:
        sesion.close()
        engine.dispose()


@pytest.fixture
def client(db):
    """Cliente HTTP contra la app real, con la BD del test inyectada.

    No se usa `with TestClient(app)` a propósito: eso dispararía el lifespan,
    que crea las tablas en la BD de verdad y arranca el scheduler de fondo.

    Manda `Origin` por defecto porque es lo que hace un navegador en cada POST,
    y desde [MC-A6] la comprobación de origen falla cerrada: sin esa cabecera
    todos los POST de la suite serían 403, que no es lo que ninguno de ellos
    quiere probar. Los tests que sí van sobre el origen la retiran a mano
    (ver `test_csrf.py::limpiar`).
    """
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, headers={"origin": "http://testserver"})
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def crear_item(db):
    """Alta directa de un ítem, saltándose la capa HTTP."""
    def _crear(**campos):
        campos.setdefault("media_type", MediaType.LIBRO)
        campos.setdefault("title", "Titulo de prueba")
        campos.setdefault("status", MediaStatus.PENDIENTE)
        item = MediaItem(**campos)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    return _crear


@pytest.fixture
def listas_dinamicas(db):
    """Siembra las 4 vistas automáticas por estado, como hace el lifespan
    real en producción, y las devuelve indexadas por `filtro_estado`."""
    from app.routers.lists import seed_smart_lists
    seed_smart_lists(db)
    return {x.filtro_estado: x for x in db.query(Lista).filter(Lista.filtro_estado.isnot(None))}


@pytest.fixture
def crear_serie(db):
    """Serie con episodios, para los tests de progreso y de 'próximamente'."""
    def _crear(temporadas=2, por_temporada=3, vistos=0, **campos):
        campos.setdefault("media_type", MediaType.SERIE)
        campos.setdefault("title", "Serie de prueba")
        campos.setdefault("status", MediaStatus.EN_PROGRESO)
        serie = MediaItem(**campos)
        restantes = vistos
        for t in range(1, temporadas + 1):
            for n in range(1, por_temporada + 1):
                marcado = restantes > 0
                restantes -= 1
                serie.episodes.append(Episode(
                    season_number=t, episode_number=n,
                    name=f"Episodio {n}", runtime_minutes=45, watched=marcado,
                ))
        db.add(serie)
        db.commit()
        db.refresh(serie)
        return serie
    return _crear
