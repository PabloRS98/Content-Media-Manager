"""[N7] Dónde tienes cada cosa: Netflix, Kindle, Steam, el estante del salón.

La pregunta que responde no es de coleccionista, es de suscripción: "¿qué me
queda pendiente en Netflix antes de darme de baja?". Sin el campo hay que
recordarlo de memoria, y con doscientos pendientes no se recuerda.
"""
from app.models import MediaItem, MediaStatus, MediaType


class TestGuardarLaPlataforma:
    def test_se_guarda_al_crear(self, client, db, usuario):
        client.post("/agregar", data={
            "title": "The Bear", "media_type": "serie", "status": "pendiente",
            "plataforma": "Disney+",
        })

        item = db.query(MediaItem).filter(MediaItem.title == "The Bear").one()
        assert item.plataforma == "Disney+"

    def test_se_puede_editar_y_vaciar(self, client, db, crear_item):
        item = crear_item(title="Dune", plataforma="Kindle")

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Dune", "media_type": "libro", "status": "pendiente",
            "plataforma": "",
        })
        db.refresh(item)
        # Vacío es None, no "": si no, el desplegable del filtro se llenaría de
        # una opción en blanco y "sin plataforma" dejaría de poder distinguirse.
        assert item.plataforma is None

    def test_los_espacios_no_crean_plataformas_distintas(self, client, db, crear_item):
        item = crear_item(title="Celeste")

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Celeste", "media_type": "videojuego", "status": "pendiente",
            "plataforma": "  Steam  ",
        })
        db.refresh(item)
        assert item.plataforma == "Steam"


class TestFiltrarPorPlataforma:
    def test_filtra_lo_pendiente_de_un_servicio(self, client, crear_item):
        """El caso de uso literal: qué me queda en Netflix."""
        crear_item(title="En Netflix", media_type=MediaType.PELICULA,
                   status=MediaStatus.PENDIENTE, plataforma="Netflix")
        crear_item(title="En Filmin", media_type=MediaType.PELICULA,
                   status=MediaStatus.PENDIENTE, plataforma="Filmin")
        crear_item(title="Ya vista en Netflix", media_type=MediaType.PELICULA,
                   status=MediaStatus.COMPLETADO, plataforma="Netflix")

        html = client.get("/catalogo?tipo=pelicula&plataforma=Netflix&estado=pendiente").text

        assert "En Netflix" in html
        assert "En Filmin" not in html
        assert "Ya vista en Netflix" not in html

    def test_es_exacto_no_por_trozos(self, client, crear_item):
        """"Movistar" no puede arrastrar a "Movistar Plus+": son suscripciones
        distintas, y con LIKE una se comería a la otra."""
        crear_item(title="La de una", media_type=MediaType.PELICULA, plataforma="Movistar")
        crear_item(title="La de la otra", media_type=MediaType.PELICULA,
                   plataforma="Movistar Plus+")

        html = client.get("/catalogo?tipo=pelicula&plataforma=Movistar").text

        assert "La de una" in html
        assert "La de la otra" not in html

    def test_el_desplegable_ofrece_las_del_propio_catalogo(self, client, crear_item):
        crear_item(title="A", media_type=MediaType.PELICULA, plataforma="Netflix")
        crear_item(title="B", media_type=MediaType.PELICULA, plataforma="Filmin")
        crear_item(title="C", media_type=MediaType.PELICULA, plataforma="Netflix")

        html = client.get("/catalogo?tipo=pelicula").text

        assert "plataforma=Netflix" in html
        assert "plataforma=Filmin" in html

    def test_no_ofrece_las_de_otra_cuenta(self, client, crear_item, otro_usuario, db):
        crear_item(title="Mía", media_type=MediaType.PELICULA, plataforma="Filmin")
        db.add(MediaItem(usuario_id=otro_usuario.id, title="Suya",
                         media_type=MediaType.PELICULA, status=MediaStatus.PENDIENTE,
                         plataforma="HBO"))
        db.commit()

        html = client.get("/catalogo?tipo=pelicula").text

        assert "plataforma=Filmin" in html
        assert "HBO" not in html

    def test_una_plataforma_inexistente_no_revienta(self, client, crear_item):
        crear_item(title="Algo", media_type=MediaType.PELICULA)
        respuesta = client.get("/catalogo?tipo=pelicula&plataforma=NoExiste")
        assert respuesta.status_code == 200
        assert "Algo" not in respuesta.text


class TestFichaDeDetalle:
    def test_la_ficha_ensena_la_plataforma(self, client, crear_item):
        item = crear_item(title="Severance", plataforma="Apple TV+")

        html = client.get("/item/%d" % item.id).text

        assert "Apple TV+" in html

    def test_sin_plataforma_no_pinta_nada(self, client, crear_item):
        item = crear_item(title="Sin plataforma")
        html = client.get("/item/%d" % item.id).text
        assert "plataforma-badge" not in html
