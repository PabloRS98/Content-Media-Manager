"""[N5] Diario: qué hiciste y cuándo.

Las estadísticas anuales solo tienen sentido en diciembre. Esto es lo mismo
durante el año: "esta semana viste cuatro episodios de X, terminaste el libro Y
y empezaste el juego Z".

Todo sale de fechas que la app ya guardaba (`completed_at`, `watched_at`) más
una nueva (`started_at`), y nada se deduce de `updated_at`: esa columna cambia
al corregir una errata, y un diario que diga que empezaste un libro el día que
le arreglaste el título es un diario que miente.
"""
from datetime import date, timedelta

from app.models import Episode, MediaItem, MediaStatus, MediaType

HOY = date.today()
MES_ACTUAL = HOY.strftime("%Y-%m")


def _dia_de_este_mes(dia: int) -> date:
    return HOY.replace(day=dia)


class TestQueSaleEnElDiario:
    def test_lo_que_terminaste(self, client, crear_item):
        crear_item(title="El imperio final", status=MediaStatus.COMPLETADO,
                   completed_at=_dia_de_este_mes(3))

        html = client.get("/actividad").text

        assert "El imperio final" in html
        assert "Terminaste" in html

    def test_lo_que_empezaste(self, client, crear_item):
        crear_item(title="Hollow Knight", media_type=MediaType.VIDEOJUEGO,
                   status=MediaStatus.EN_PROGRESO, started_at=_dia_de_este_mes(5))

        html = client.get("/actividad").text

        assert "Hollow Knight" in html
        assert "Empezaste" in html

    def test_los_episodios_del_mismo_dia_se_agrupan(self, client, db, usuario):
        """Una tarde de maratón son cinco líneas idénticas o una que dice
        "5 episodios". La segunda es la que se lee."""
        serie = MediaItem(usuario_id=usuario.id, title="Severance",
                          media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
        for n in range(1, 6):
            serie.episodes.append(Episode(season_number=1, episode_number=n,
                                          watched=True, watched_at=_dia_de_este_mes(7)))
        db.add(serie)
        db.commit()

        html = client.get("/actividad").text

        assert "5 episodios" in html
        assert html.count("Severance") == 1

    def test_dos_dias_distintos_no_se_mezclan(self, client, db, usuario):
        serie = MediaItem(usuario_id=usuario.id, title="Severance",
                          media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      watched=True, watched_at=_dia_de_este_mes(7)))
        serie.episodes.append(Episode(season_number=1, episode_number=2,
                                      watched=True, watched_at=_dia_de_este_mes(8)))
        db.add(serie)
        db.commit()

        html = client.get("/actividad").text

        assert html.count("Severance") == 2
        assert "1 episodio" in html

    def test_un_episodio_sin_fecha_no_inventa_un_dia(self, client, db, usuario):
        """Los episodios marcados antes de que existiera `watched_at` no tienen
        fecha. Sin esto acabarían todos amontonados en un día cualquiera."""
        serie = MediaItem(usuario_id=usuario.id, title="Serie vieja",
                          media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      watched=True, watched_at=None))
        db.add(serie)
        db.commit()

        assert "Serie vieja" not in client.get("/actividad").text


class TestElMes:
    def test_por_defecto_el_mes_en_curso(self, client, crear_item):
        crear_item(title="De este mes", status=MediaStatus.COMPLETADO,
                   completed_at=_dia_de_este_mes(2))
        crear_item(title="De hace tiempo", status=MediaStatus.COMPLETADO,
                   completed_at=HOY - timedelta(days=400))

        html = client.get("/actividad").text

        assert "De este mes" in html
        assert "De hace tiempo" not in html

    def test_se_puede_pedir_otro_mes(self, client, crear_item):
        hace_dos_meses = (HOY.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)
        crear_item(title="De hace dos meses", status=MediaStatus.COMPLETADO,
                   completed_at=hace_dos_meses)

        html = client.get("/actividad?mes=%s" % hace_dos_meses.strftime("%Y-%m")).text

        assert "De hace dos meses" in html

    def test_un_mes_vacio_lo_dice_en_vez_de_reventar(self, client):
        respuesta = client.get("/actividad?mes=1999-01")
        assert respuesta.status_code == 200
        assert "Nada" in respuesta.text or "nada" in respuesta.text

    def test_un_mes_con_formato_raro_cae_al_actual(self, client, crear_item):
        """El parámetro va en la URL y se puede escribir a mano."""
        crear_item(title="De este mes", status=MediaStatus.COMPLETADO,
                   completed_at=_dia_de_este_mes(2))

        for basura in ("", "pepito", "2026-13", "2026-1-1", "9999999999-99"):
            respuesta = client.get("/actividad?mes=%s" % basura)
            assert respuesta.status_code == 200, basura
            assert "De este mes" in respuesta.text, basura

    def test_el_resumen_cuenta_lo_del_mes(self, client, db, usuario, crear_item):
        crear_item(title="Libro", status=MediaStatus.COMPLETADO,
                   completed_at=_dia_de_este_mes(2))
        serie = MediaItem(usuario_id=usuario.id, title="Serie",
                          media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
        for n in range(1, 4):
            serie.episodes.append(Episode(season_number=1, episode_number=n,
                                          watched=True, watched_at=_dia_de_este_mes(4)))
        db.add(serie)
        db.commit()

        html = client.get("/actividad").text

        assert "3 episodios" in html
        assert "1 terminado" in html


class TestFechasEnEspanol:
    """El locale de la imagen es el de por defecto, así que `strftime("%A")`
    devolvía "Thursday": era el único texto en inglés de toda la interfaz, y
    salía en el diario y en el calendario."""

    def test_el_diario_pone_los_dias_en_espanol(self, client, crear_item):
        crear_item(title="Algo", status=MediaStatus.COMPLETADO,
                   completed_at=_dia_de_este_mes(3))

        html = client.get("/actividad").text

        assert not any(d in html for d in
                       ("Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"))

    def test_el_calendario_tambien(self, client, db, usuario):
        serie = MediaItem(usuario_id=usuario.id, title="Serie",
                          media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      air_date=HOY + timedelta(days=3)))
        db.add(serie)
        db.commit()

        html = client.get("/calendario").text

        assert "Serie" in html
        assert not any(m in html for m in ("January", "August", "December"))


class TestAislamiento:
    def test_no_se_ve_la_actividad_de_otra_cuenta(self, client, db, otro_usuario):
        db.add(MediaItem(usuario_id=otro_usuario.id, title="Cosa suya",
                         media_type=MediaType.LIBRO, status=MediaStatus.COMPLETADO,
                         completed_at=_dia_de_este_mes(3)))
        db.commit()

        assert "Cosa suya" not in client.get("/actividad").text


class TestCuandoSeMarcaElComienzo:
    def test_pasar_a_en_progreso_deja_la_fecha(self, client, db, crear_item):
        item = crear_item(title="Dune", status=MediaStatus.PENDIENTE)

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Dune", "status": "en_progreso",
        })
        db.refresh(item)

        assert item.started_at == HOY

    def test_no_se_reescribe_al_volver_a_guardar(self, client, db, crear_item):
        """Editar la nota de algo que empezaste en enero no lo mueve a hoy."""
        enero = date(HOY.year, 1, 15)
        item = crear_item(title="Dune", status=MediaStatus.EN_PROGRESO, started_at=enero)

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Dune", "status": "en_progreso", "notes": "una nota nueva",
        })
        db.refresh(item)

        assert item.started_at == enero

    def test_marcar_un_episodio_tambien_lo_marca(self, client, db, usuario):
        """Una serie se empieza viendo un episodio, no editando un formulario."""
        serie = MediaItem(usuario_id=usuario.id, title="Severance",
                          media_type=MediaType.SERIE, status=MediaStatus.PENDIENTE)
        serie.episodes.append(Episode(season_number=1, episode_number=1))
        db.add(serie)
        db.commit()
        db.refresh(serie)

        client.post("/item/%d/episodio/%d/toggle" % (serie.id, serie.episodes[0].id))
        db.refresh(serie)

        assert serie.started_at == HOY
