"""Tests del seguimiento por episodios (series/podcasts) y de la pantalla de
inicio, que es la que resume el estado de todo el catálogo.
"""
from datetime import date, timedelta

from app.models import Episode, MediaStatus, MediaType, Priority
from app.services.metadata import mark_through, recompute_status, toggle_episode


class TestProgresoPorEpisodios:
    def test_cuenta_vistos_y_calcula_el_siguiente(self, crear_serie):
        serie = crear_serie(temporadas=2, por_temporada=3, vistos=4)
        stats = serie.episode_stats()

        assert stats["total"] == 6
        assert stats["watched"] == 4
        assert stats["next"].code == "S02E02"

    def test_sin_episodios_pendientes_no_hay_siguiente(self, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=2, vistos=2)
        assert serie.episode_stats()["next"] is None

    def test_marcar_un_episodio_lo_alterna(self, crear_serie, db):
        serie = crear_serie(temporadas=1, por_temporada=3)
        ep = serie.episodes[0]

        toggle_episode(serie, ep)
        assert ep.watched is True
        assert ep.watched_at == date.today()

        toggle_episode(serie, ep)
        assert ep.watched is False
        assert ep.watched_at is None

    def test_marcar_hasta_aqui_marca_todos_los_anteriores(self, crear_serie):
        serie = crear_serie(temporadas=2, por_temporada=3)
        objetivo = next(e for e in serie.episodes if e.code == "S02E02")

        mark_through(serie, objetivo)

        vistos = [e.code for e in serie.episodes if e.watched]
        assert vistos == ["S01E01", "S01E02", "S01E03", "S02E01", "S02E02"]

    def test_ver_el_ultimo_episodio_completa_la_serie(self, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=2, vistos=1)
        ultimo = serie.episodes[-1]

        toggle_episode(serie, ultimo)

        assert serie.status == MediaStatus.COMPLETADO
        assert serie.completed_at == date.today()

    def test_desmarcar_un_episodio_devuelve_la_serie_a_en_progreso(self, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=2, vistos=2)
        recompute_status(serie)
        assert serie.status == MediaStatus.COMPLETADO

        toggle_episode(serie, serie.episodes[-1])

        assert serie.status == MediaStatus.EN_PROGRESO
        assert serie.completed_at is None

    def test_una_serie_en_la_wishlist_no_cambia_de_estado(self, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=2, vistos=2,
                            status=MediaStatus.WISHLIST)
        recompute_status(serie)
        assert serie.status == MediaStatus.WISHLIST

    def test_los_endpoints_de_episodio_responden(self, client, crear_serie, db):
        serie = crear_serie(temporadas=1, por_temporada=3)
        ep = serie.episodes[0]

        r = client.post(f"/item/{serie.id}/episodio/{ep.id}/toggle", follow_redirects=False)
        assert r.status_code == 303
        db.refresh(ep)
        assert ep.watched is True

        objetivo = serie.episodes[2]
        r = client.post(f"/item/{serie.id}/marcar-hasta/{objetivo.id}", follow_redirects=False)
        assert r.status_code == 303
        db.refresh(serie)
        assert sum(1 for e in serie.episodes if e.watched) == 3

    def test_un_episodio_de_otra_serie_no_se_puede_marcar(self, client, crear_serie, db):
        una = crear_serie(title="Una")
        otra = crear_serie(title="Otra")
        ajeno = otra.episodes[0]

        client.post(f"/item/{una.id}/episodio/{ajeno.id}/toggle", follow_redirects=False)

        db.refresh(ajeno)
        assert ajeno.watched is False


class TestFichaDeDetalle:
    def test_muestra_los_episodios_agrupados_por_temporada(self, client, crear_serie):
        serie = crear_serie(temporadas=2, por_temporada=3)
        html = client.get(f"/item/{serie.id}").text

        assert "S01E01" in html
        assert "S02E03" in html

    def test_relaciona_los_titulos_de_la_misma_saga(self, client, crear_item):
        uno = crear_item(title="Parte I", saga="Mi saga", media_type=MediaType.PELICULA)
        crear_item(title="Parte II", saga="Mi saga", media_type=MediaType.PELICULA)

        html = client.get(f"/item/{uno.id}").text
        assert "Parte II" in html


class TestInicio:
    def test_resume_el_estado_del_catalogo(self, client, crear_item):
        crear_item(title="Leyendo", status=MediaStatus.EN_PROGRESO)
        crear_item(title="Por leer", status=MediaStatus.PENDIENTE)
        crear_item(title="Leido", status=MediaStatus.COMPLETADO,
                   completed_at=date.today())
        crear_item(title="Lo quiero", status=MediaStatus.WISHLIST)

        html = client.get("/").text
        for titulo in ("Leyendo", "Por leer", "Leido", "Lo quiero"):
            assert titulo in html

    def test_un_catalogo_vacio_muestra_la_pantalla_de_bienvenida(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_los_pendientes_salen_ordenados_por_prioridad(self, client, crear_item):
        crear_item(title="Poco urgente", priority=Priority.BAJA)
        crear_item(title="Muy urgente", priority=Priority.ALTA)

        html = client.get("/").text
        assert html.index("Muy urgente") < html.index("Poco urgente")

    def test_proximamente_lista_los_episodios_futuros(self, client, crear_serie, db):
        serie = crear_serie(temporadas=1, por_temporada=1, title="Mi serie",
                            status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(
            season_number=1, episode_number=2, name="El que viene",
            air_date=date.today() + timedelta(days=7),
        ))
        db.commit()

        html = client.get("/").text
        # La home lista el título de la serie y el código del episodio
        assert "Mi serie" in html
        assert "S01E02" in html

    def test_proximamente_ignora_lo_ya_emitido(self, client, crear_serie, db):
        serie = crear_serie(temporadas=1, por_temporada=1, status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(
            season_number=1, episode_number=9, name="El de la semana pasada",
            air_date=date.today() - timedelta(days=7),
        ))
        db.commit()

        html = client.get("/calendario").text
        assert "S01E09" not in html

    def test_el_calendario_agrupa_por_fecha(self, client, crear_item):
        crear_item(title="Estreno esperado", status=MediaStatus.WISHLIST,
                   media_type=MediaType.PELICULA,
                   release_date=date.today() + timedelta(days=30))

        assert "Estreno esperado" in client.get("/calendario").text


class TestTengoTiempo:
    def test_sugiere_solo_lo_que_cabe_en_el_hueco(self, client, crear_item):
        crear_item(title="Corta", media_type=MediaType.PELICULA,
                   runtime_minutes=80, status=MediaStatus.PENDIENTE)
        crear_item(title="Larguisima", media_type=MediaType.PELICULA,
                   runtime_minutes=200, status=MediaStatus.PENDIENTE)

        html = client.get("/tengo-tiempo?minutos=90").text
        assert "Corta" in html
        assert "Larguisima" not in html

    def test_no_propone_nada_ya_completado(self, client, crear_item):
        crear_item(title="Ya vista", media_type=MediaType.PELICULA,
                   runtime_minutes=80, status=MediaStatus.COMPLETADO)

        assert "Ya vista" not in client.get("/tengo-tiempo?minutos=200").text

    def test_acota_los_minutos_a_un_rango_razonable(self, client):
        assert client.get("/tengo-tiempo?minutos=-50").status_code == 200
        assert client.get("/tengo-tiempo?minutos=999999").status_code == 200


class TestListas:
    def test_crear_una_lista_y_meterle_un_item(self, client, crear_item, db):
        item = crear_item(title="Para ver juntos")

        client.post("/listas", data={"name": "Con la pareja"}, follow_redirects=False)
        lista = db.query(__import__("app.models", fromlist=["Lista"]).Lista).one()

        client.post(f"/item/{item.id}/anadir-lista",
                    data={"list_id": str(lista.id)}, follow_redirects=False)

        db.refresh(lista)
        assert item in lista.items

    def test_no_se_repite_el_nombre_de_lista(self, client, db):
        from app.models import Lista
        client.post("/listas", data={"name": "Unica"}, follow_redirects=False)
        client.post("/listas", data={"name": "Unica"}, follow_redirects=False)

        assert db.query(Lista).filter(Lista.name == "Unica").count() == 1

    def test_una_lista_sin_nombre_se_rechaza(self, client, db):
        from app.models import Lista
        r = client.post("/listas", data={"name": "   "}, follow_redirects=False)

        assert r.status_code == 303
        assert db.query(Lista).count() == 0


class TestSalud:
    def test_el_endpoint_de_salud_responde(self, client):
        r = client.get("/salud")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
