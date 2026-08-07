"""Avisos de Telegram: escapado del texto y marcado del flag.

Los dos problemas van en el mismo camino y se refuerzan: un título con `&`
hace que Telegram responda 400, el error se traga, y el episodio queda marcado
como notificado igualmente. El aviso no llega **y no se reintenta nunca**.

Títulos reales que lo disparan: `Marley & Me`, `Will & Grace`,
`Sanford & Son`, `Dungeons & Dragons`.
"""
from datetime import date, timedelta

import pytest

from app.models import Episode, MediaItem, MediaStatus, MediaType
from app.services import scheduler, telegram

AYER = date.today() - timedelta(days=1)


@pytest.fixture
def envios(monkeypatch):
    """Sustituye `telegram.send_message` y guarda lo que se le pasa.

    `exito` decide qué devuelve, que es la mitad del hallazgo: el código
    marcaba el flag sin mirar el resultado.
    """
    enviados = []

    class Espia:
        exito = True

        def __call__(self, texto):
            enviados.append(texto)
            return self.exito

    espia = Espia()
    monkeypatch.setattr(scheduler.telegram, "send_message", espia)
    espia.enviados = enviados
    return espia


@pytest.fixture
def serie_con_episodio_emitido(db):
    def _crear(titulo="Marley & Me", nombre_ep="Chapter <one>"):
        serie = MediaItem(
            title=titulo, media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO
        )
        serie.episodes.append(
            Episode(season_number=1, episode_number=1, name=nombre_ep,
                    air_date=AYER, notified=False)
        )
        db.add(serie)
        db.commit()
        db.refresh(serie)
        return serie
    return _crear


class TestEscapadoDelTexto:
    def test_el_aviso_de_episodio_escapa_el_titulo(self, db, envios, serie_con_episodio_emitido):
        serie_con_episodio_emitido()
        scheduler.check_new_episodes(db)

        assert len(envios.enviados) == 1
        texto = envios.enviados[0]
        assert "Marley &amp; Me" in texto
        # El `&` crudo solo puede aparecer como parte de una entidad.
        assert "Marley & Me" not in texto

    def test_el_aviso_escapa_tambien_el_nombre_del_episodio(
        self, db, envios, serie_con_episodio_emitido
    ):
        serie_con_episodio_emitido(titulo="Serie normal", nombre_ep="Chapter <one>")
        scheduler.check_new_episodes(db)
        assert "Chapter &lt;one&gt;" in envios.enviados[0]

    def test_el_aviso_de_estreno_escapa_el_titulo(self, db, envios):
        db.add(MediaItem(
            title="Dungeons & Dragons", media_type=MediaType.PELICULA,
            status=MediaStatus.WISHLIST, release_date=AYER, release_notified=False,
        ))
        db.commit()
        scheduler.check_releases(db)
        assert "Dungeons &amp; Dragons" in envios.enviados[0]

    def test_las_etiquetas_del_propio_mensaje_se_conservan(
        self, db, envios, serie_con_episodio_emitido
    ):
        """Escapar el valor, no el mensaje: el `<b>` del formato es nuestro."""
        serie_con_episodio_emitido(titulo="Serie normal", nombre_ep="Piloto")
        scheduler.check_new_episodes(db)
        assert "<b>Serie normal</b>" in envios.enviados[0]


class TestMarcadoCondicional:
    def test_el_episodio_no_se_marca_si_el_envio_falla(
        self, db, envios, serie_con_episodio_emitido
    ):
        """Sin esto el aviso se pierde para siempre: `notified` queda a True y
        el episodio no vuelve a mirarse jamás."""
        envios.exito = False
        serie = serie_con_episodio_emitido()

        assert scheduler.check_new_episodes(db) == 0
        db.refresh(serie)
        assert serie.episodes[0].notified is False

    def test_el_episodio_si_se_marca_cuando_el_envio_funciona(
        self, db, envios, serie_con_episodio_emitido
    ):
        serie = serie_con_episodio_emitido()
        assert scheduler.check_new_episodes(db) == 1
        db.refresh(serie)
        assert serie.episodes[0].notified is True

    def test_el_estreno_no_se_marca_si_el_envio_falla(self, db, envios):
        envios.exito = False
        item = MediaItem(
            title="Peli pendiente", media_type=MediaType.PELICULA,
            status=MediaStatus.WISHLIST, release_date=AYER, release_notified=False,
        )
        db.add(item)
        db.commit()

        assert scheduler.check_releases(db) == 0
        db.refresh(item)
        assert item.release_notified is False

    def test_un_fallo_se_reintenta_en_el_siguiente_ciclo(
        self, db, envios, serie_con_episodio_emitido
    ):
        """Que es el motivo entero de no marcar cuando falla."""
        envios.exito = False
        serie_con_episodio_emitido()
        scheduler.check_new_episodes(db)

        envios.exito = True
        assert scheduler.check_new_episodes(db) == 1


def test_avisar_de_episodios_no_hace_n_mas_uno(db, envios):
    """El `join(MediaItem)` sirve para filtrar, pero no CARGA la relación:
    cada `ep.item.title` del bucle disparaba un SELECT adicional. Tras un fin
    de semana sin correr el job, con 40 episodios pendientes, eran 40 consultas
    de más -- el mismo N+1 que `catalog.stats` ya resolvió con joinedload."""
    from sqlalchemy import event

    for n in range(20):
        serie = MediaItem(
            title="Serie %02d" % n, media_type=MediaType.SERIE,
            status=MediaStatus.EN_PROGRESO,
        )
        serie.episodes.append(
            Episode(season_number=1, episode_number=1, name="Piloto",
                    air_date=AYER, notified=False)
        )
        db.add(serie)
    db.commit()

    lecturas: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        if sentencia.lstrip().upper().startswith("SELECT"):
            lecturas.append(sentencia)

    motor = db.get_bind()
    event.listen(motor, "before_cursor_execute", _antes)
    try:
        assert scheduler.check_new_episodes(db) == 20
    finally:
        event.remove(motor, "before_cursor_execute", _antes)

    assert len(lecturas) <= 2, (
        "%d consultas para 20 episodios: el número crece con N.\n%s"
        % (len(lecturas), "\n".join(s.replace("\n", " ")[:90] for s in lecturas[:4]))
    )


def test_send_message_devuelve_false_sin_configurar(monkeypatch):
    """El contrato del que depende todo lo anterior."""
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "")
    assert telegram.send_message("hola") is False
