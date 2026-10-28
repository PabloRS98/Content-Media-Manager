"""Lo que se puede hacer desde la ficha sin abrir el formulario de edición:
cambiar el estado, poner la nota con las estrellas y apuntar por dónde vas.

Las tres cosas pasan por las mismas reglas que el formulario completo (fechas
del diario, nota 1-10, cuenta propia); estos tests vigilan que no se las salten
por ir por un atajo.
"""
from datetime import date

import pytest

from app.models import MediaItem, MediaStatus, MediaType
from app.templating import static_url


class TestCambioDeEstado:
    def test_cambia_el_estado(self, client, db, crear_item):
        item = crear_item(title="Algo", status=MediaStatus.PENDIENTE)
        r = client.post("/item/%d/estado" % item.id, data={"status": "en_progreso"}, follow_redirects=False)
        assert r.status_code == 303
        db.refresh(item)
        assert item.status == MediaStatus.EN_PROGRESO

    def test_empezar_apunta_la_fecha_para_el_diario(self, client, db, crear_item):
        item = crear_item(title="Algo", status=MediaStatus.PENDIENTE)
        client.post("/item/%d/estado" % item.id, data={"status": "en_progreso"})
        db.refresh(item)
        assert item.started_at == date.today()

    def test_terminar_apunta_la_fecha_solo_la_primera_vez(self, client, db, crear_item):
        antes = date(2020, 1, 1)
        item = crear_item(title="Algo", status=MediaStatus.EN_PROGRESO, completed_at=antes)
        client.post("/item/%d/estado" % item.id, data={"status": "completado"})
        db.refresh(item)
        assert item.status == MediaStatus.COMPLETADO
        assert item.completed_at == antes

        otro = crear_item(title="Otro", status=MediaStatus.EN_PROGRESO)
        client.post("/item/%d/estado" % otro.id, data={"status": "completado"})
        db.refresh(otro)
        assert otro.completed_at == date.today()

    def test_un_estado_inventado_no_cambia_nada(self, client, db, crear_item):
        item = crear_item(title="Algo", status=MediaStatus.PENDIENTE)
        r = client.post("/item/%d/estado" % item.id, data={"status": "loquesea"})
        assert r.status_code == 200
        db.refresh(item)
        assert item.status == MediaStatus.PENDIENTE

    def test_no_toca_items_de_otra_cuenta(self, client, db, otro_usuario):
        ajeno = MediaItem(usuario_id=otro_usuario.id, title="Ajeno",
                          media_type=MediaType.LIBRO, status=MediaStatus.PENDIENTE)
        db.add(ajeno)
        db.commit()
        client.post("/item/%d/estado" % ajeno.id, data={"status": "completado"})
        db.refresh(ajeno)
        assert ajeno.status == MediaStatus.PENDIENTE

    def test_la_ficha_marca_el_estado_actual(self, client, crear_item):
        item = crear_item(title="Algo", media_type=MediaType.LIBRO, status=MediaStatus.EN_PROGRESO)
        html = client.get("/item/%d" % item.id).text
        assert 'value="en_progreso" class="st-en_progreso" aria-pressed="true"' in html
        # Con las palabras de su tipo: un libro se lee, no se ve.
        assert "Leyendo" in html


class TestNota:
    def test_pone_y_quita_la_nota(self, client, db, crear_item):
        item = crear_item(title="Algo")
        client.post("/item/%d/nota" % item.id, data={"rating": "7"})
        db.refresh(item)
        assert item.rating == 7

        client.post("/item/%d/nota" % item.id, data={"rating": ""})
        db.refresh(item)
        assert item.rating is None

    @pytest.mark.parametrize("valor", ["0", "11", "99", "-3", "siete"])
    def test_fuera_de_rango_queda_sin_nota(self, client, db, crear_item, valor):
        item = crear_item(title="Algo", rating=5)
        client.post("/item/%d/nota" % item.id, data={"rating": valor})
        db.refresh(item)
        assert item.rating is None

    def test_las_estrellas_dicen_la_nota_en_medias(self, client, crear_item):
        crear_item(title="Algo", rating=7, status=MediaStatus.COMPLETADO)
        html = client.get("/catalogo").text
        assert "3,5 de 5" in html


class TestProgreso:
    def test_fija_el_valor(self, client, db, crear_item):
        item = crear_item(title="Libro", progress_total=300, progress_unit="páginas",
                          status=MediaStatus.EN_PROGRESO)
        client.post("/item/%d/progreso" % item.id, data={"valor": "120"})
        db.refresh(item)
        assert item.progress_current == 120

    def test_suma_sin_pasarse_del_total(self, client, db, crear_item):
        item = crear_item(title="Libro", progress_current=295, progress_total=300,
                          status=MediaStatus.EN_PROGRESO)
        client.post("/item/%d/progreso" % item.id, data={"incremento": "10"})
        db.refresh(item)
        assert item.progress_current == 300
        # Llegar al final no lo da por terminado: eso lo decide la persona.
        assert item.status == MediaStatus.EN_PROGRESO

    def test_avanzar_en_algo_pendiente_lo_empieza(self, client, db, crear_item):
        item = crear_item(title="Libro", progress_total=300, status=MediaStatus.PENDIENTE)
        client.post("/item/%d/progreso" % item.id, data={"incremento": "1"})
        db.refresh(item)
        assert item.status == MediaStatus.EN_PROGRESO
        assert item.started_at == date.today()

    @pytest.mark.parametrize("valor", ["nan", "inf", "-inf"])
    def test_numeros_no_finitos_se_rechazan(self, client, db, crear_item, valor):
        item = crear_item(title="Libro", progress_current=10, progress_total=300)
        client.post("/item/%d/progreso" % item.id, data={"valor": valor})
        db.refresh(item)
        assert item.progress_current == 10

    def test_nunca_negativo(self, client, db, crear_item):
        item = crear_item(title="Libro", progress_current=3, progress_total=300)
        client.post("/item/%d/progreso" % item.id, data={"incremento": "-10"})
        db.refresh(item)
        assert item.progress_current == 0


class TestEpisodiosConAncla:
    def test_al_marcar_vuelve_al_episodio(self, client, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=3)
        ep = serie.episodes[1]
        r = client.post("/item/%d/episodio/%d/toggle" % (serie.id, ep.id), follow_redirects=False)
        assert r.headers["location"] == "/item/%d#ep-%d" % (serie.id, ep.id)
        assert 'id="ep-%d"' % ep.id in client.get("/item/%d" % serie.id).text


class TestEstaticosVersionados:
    def test_la_url_lleva_la_huella_del_contenido(self):
        url = static_url("css/style.css")
        assert url.startswith("/static/css/style.css?v=")
        assert len(url.rsplit("=", 1)[1]) == 12

    def test_un_fichero_que_no_existe_sale_sin_huella(self):
        assert static_url("no/existe.css") == "/static/no/existe.css"

    def test_las_paginas_piden_los_estaticos_con_huella(self, client):
        html = client.get("/").text
        assert static_url("css/style.css") in html
        assert static_url("js/app.js") in html

    def test_con_huella_se_cachean_un_año(self, client):
        r = client.get(static_url("css/style.css"))
        assert "immutable" in r.headers["cache-control"]
        assert "max-age=31536000" in r.headers["cache-control"]

    def test_sin_huella_se_revalidan(self, client):
        assert client.get("/static/css/style.css").headers["cache-control"] == "no-cache"

    def test_las_vistas_no_se_cachean_como_estaticos(self, client):
        assert "immutable" not in client.get("/").headers.get("cache-control", "")


class TestNavegacion:
    def test_el_icono_de_podcast_existe(self, client):
        """Antes la barra pedía un icono "podcast" que no estaba en _icons.html
        y salía el de alerta en su lugar."""
        from app.templating import templates
        html = templates.env.get_template("_icons.html").module.icon("podcast")
        alerta = templates.env.get_template("_icons.html").module.icon("circle-alert")
        assert str(html) != str(alerta)

    def test_la_pagina_actual_se_marca_para_lectores_de_pantalla(self, client, usuario):
        """El fixture `client` se salta `usuario_actual`, que es quien deja la
        cuenta en `request.state`; sin ella la navegación no se pinta. Aquí se
        hace lo mismo que la dependencia real."""
        from fastapi import Request

        from app.cuentas import usuario_actual
        from app.main import app

        def con_cuenta(request: Request):
            request.state.cuenta_abierta = usuario
            return usuario

        app.dependency_overrides[usuario_actual] = con_cuenta
        html = client.get("/listas").text
        assert '<a href="/listas" class="navlink active" aria-current="page">' in html
        assert html.count('aria-current="page"') >= 1
