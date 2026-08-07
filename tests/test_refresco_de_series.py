"""El refresco diario de series no debe pedir lo que no puede haber cambiado.

Con 30 series seguidas de 5 temporadas de media eran 30 + 150 = 180 peticiones
HTTP secuenciales con 10 s de timeout cada una. Y `load_episodes` pedía TODAS
las temporadas cada vez, incluidas las que terminaron hace años y no van a
cambiar nunca.

El job corre a las 9:00 y también 25 s después de arrancar, así que un
reinicio del contenedor disparaba la tanda entera.
"""
import time
from datetime import date, timedelta

import pytest

from app.models import Episode, MediaItem, MediaStatus, MediaType
from app.services import metadata, scheduler, tmdb
from app.services.scheduler import temporadas_que_pueden_cambiar

HOY = date.today()


def serie_con_temporadas(db, temporadas: dict[int, date | None], titulo="Serie"):
    """Crea una serie cuyos episodios tienen las fechas indicadas por temporada."""
    item = MediaItem(
        title=titulo, media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO,
        external_source="tmdb", external_id="1234",
    )
    for sn, fecha in temporadas.items():
        item.episodes.append(Episode(season_number=sn, episode_number=1, air_date=fecha))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


class TestQueTemporadasSePiden:
    def test_no_se_piden_temporadas_terminadas(self, db):
        """Una serie cuya última temporada emitió hace 3 años no genera ninguna
        petición más allá de la comprobación de la última."""
        item = serie_con_temporadas(db, {
            1: HOY - timedelta(days=1500),
            2: HOY - timedelta(days=1100),
            3: HOY - timedelta(days=1095),
        })
        # Solo la última, por si TMDB anuncia una renovación.
        assert temporadas_que_pueden_cambiar(item, [1, 2, 3]) == [3]

    def test_se_pide_la_temporada_con_episodios_sin_emitir(self, db):
        item = serie_con_temporadas(db, {
            1: HOY - timedelta(days=900),
            2: HOY + timedelta(days=7),
        })
        assert temporadas_que_pueden_cambiar(item, [1, 2]) == [2]

    def test_se_pide_la_temporada_emitida_hace_poco(self, db):
        """Dentro de la ventana: aún puede recibir episodios."""
        item = serie_con_temporadas(db, {
            1: HOY - timedelta(days=900),
            2: HOY - timedelta(days=5),
        })
        assert temporadas_que_pueden_cambiar(item, [1, 2]) == [2]

    def test_se_pide_una_temporada_que_no_tenemos(self, db):
        item = serie_con_temporadas(db, {1: HOY - timedelta(days=900)})
        assert temporadas_que_pueden_cambiar(item, [1, 2]) == [2]

    def test_se_pide_una_temporada_sin_fechas_conocidas(self, db):
        """Sin `air_date` no se puede afirmar que esté cerrada."""
        item = serie_con_temporadas(db, {1: None})
        assert temporadas_que_pueden_cambiar(item, [1]) == [1]

    def test_una_serie_sin_episodios_pide_todo(self, db):
        item = serie_con_temporadas(db, {})
        assert temporadas_que_pueden_cambiar(item, [1, 2, 3]) == [1, 2, 3]

    def test_sin_temporadas_remotas_no_se_pide_nada(self, db):
        item = serie_con_temporadas(db, {1: HOY})
        assert temporadas_que_pueden_cambiar(item, []) == []


class TestRefrescoCompleto:
    @pytest.fixture
    def fabrica_de_sesiones(self, db):
        """Una sesión nueva por hilo, contra la misma base del test.

        Es lo que hace producción (`SessionLocal`), y hace falta de verdad:
        compartir una sola sesión entre hilos hace que el `rollback()` de una
        serie que falla se lleve por delante el trabajo de las demás.
        """
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    @pytest.fixture
    def tmdb_falso(self, monkeypatch):
        """Registra qué temporadas se piden y cuánto tarda cada llamada."""
        pedidas: list[tuple[str, int]] = []

        monkeypatch.setattr(scheduler.settings, "tmdb_api_key", "clave")
        monkeypatch.setattr(
            tmdb, "get_tv_details",
            lambda ext_id: {"seasons": [1, 2, 3]},
        )

        def _load(db_, item, temporadas):
            for sn in temporadas:
                pedidas.append((item.title, sn))
                time.sleep(0.2)
            return 0

        monkeypatch.setattr(metadata, "load_episodes", _load)
        return pedidas

    def test_una_serie_terminada_no_consume_peticiones_de_temporadas_viejas(
        self, db, tmdb_falso, fabrica_de_sesiones
    ):
        serie_con_temporadas(db, {
            1: HOY - timedelta(days=1500),
            2: HOY - timedelta(days=1200),
            3: HOY - timedelta(days=1095),
        }, titulo="Terminada")

        scheduler.refresh_following_episodes(db, session_factory=fabrica_de_sesiones)

        assert tmdb_falso == [("Terminada", 3)], tmdb_falso

    def test_las_series_se_refrescan_en_paralelo(self, db, tmdb_falso, fabrica_de_sesiones):
        """8 series terminadas: una temporada cada una, 0,2 s por petición.
        En serie son 1,6 s; con 4 hilos, ~0,4 s."""
        viejas = {
            1: HOY - timedelta(days=1500),
            2: HOY - timedelta(days=1200),
            3: HOY - timedelta(days=1095),
        }
        for n in range(8):
            serie_con_temporadas(db, viejas, titulo="Serie %02d" % n)

        inicio = time.monotonic()
        scheduler.refresh_following_episodes(db, session_factory=fabrica_de_sesiones)
        transcurrido = time.monotonic() - inicio

        assert len(tmdb_falso) == 8, tmdb_falso
        assert transcurrido < 1.0, (
            "%.2f s para 8 series: se siguen refrescando en serie" % transcurrido
        )

    def test_un_fallo_en_una_serie_no_pierde_las_demas(
        self, db, tmdb_falso, fabrica_de_sesiones, monkeypatch
    ):
        """Antes había un único commit al final: si la 25ª reventaba, se
        perdían las 24 anteriores."""
        viejas = {
            1: HOY - timedelta(days=1500),
            2: HOY - timedelta(days=1200),
            3: HOY - timedelta(days=1095),
        }
        for n in range(4):
            serie_con_temporadas(db, viejas, titulo="Serie %02d" % n)

        registra = metadata.load_episodes  # el doble que puso tmdb_falso

        def _revienta(db_, item, temporadas):
            if item.title == "Serie 02":
                raise RuntimeError("TMDB caída")
            return registra(db_, item, temporadas)

        monkeypatch.setattr(metadata, "load_episodes", _revienta)

        scheduler.refresh_following_episodes(db, session_factory=fabrica_de_sesiones)

        procesadas = {titulo for titulo, _ in tmdb_falso}
        assert procesadas == {"Serie 00", "Serie 01", "Serie 03"}, tmdb_falso

    def test_sin_clave_de_tmdb_no_hace_nada(self, db, monkeypatch, fabrica_de_sesiones):
        monkeypatch.setattr(scheduler.settings, "tmdb_api_key", "")
        serie_con_temporadas(db, {1: HOY})
        scheduler.refresh_following_episodes(db, session_factory=fabrica_de_sesiones)


def test_el_job_de_avisos_no_se_solapa_consigo_mismo():
    """El job corre a las 9:00 y 25 s después de arrancar. Sin max_instances,
    un reinicio durante una tanda larga deja dos ejecuciones a la vez."""
    import inspect

    fuente = inspect.getsource(scheduler.start_scheduler)
    assert "max_instances=1" in fuente
