"""Vistas automáticas por estado ('En progreso', 'Pendientes', 'Completados',
'Wishlist'): dan destino real, en la pestaña Listas, a los accesos rápidos de
inicio. A diferencia de una lista manual, su contenido se calcula en vivo por
`MediaItem.status`, no se guarda -- por eso no admiten añadir/quitar a mano
ni borrarlas.
"""
from app.models import Lista, MediaStatus, MediaType
from app.routers.lists import seed_smart_lists


class TestSembradoDeVistasAutomaticas:
    def test_crea_las_4_vistas(self, db):
        seed_smart_lists(db)
        dinamicas = db.query(Lista).filter(Lista.filtro_estado.isnot(None)).all()
        assert {x.filtro_estado for x in dinamicas} == {
            "en_progreso", "pendiente", "completado", "wishlist",
        }

    def test_es_idempotente(self, db):
        seed_smart_lists(db)
        seed_smart_lists(db)
        assert db.query(Lista).filter(Lista.filtro_estado.isnot(None)).count() == 4

    def test_evita_chocar_con_el_nombre_de_una_lista_manual(self, db):
        db.add(Lista(name="Wishlist"))  # el usuario ya tenía una lista manual con ese nombre
        db.commit()

        seed_smart_lists(db)

        manual = db.query(Lista).filter(Lista.name == "Wishlist").one()
        assert manual.filtro_estado is None
        automatica = db.query(Lista).filter(Lista.filtro_estado == "wishlist").one()
        assert automatica.name != "Wishlist"


class TestVistaAutomatica:
    def test_muestra_solo_los_items_de_su_estado_cruzando_tipos(self, client, crear_item, listas_dinamicas):
        crear_item(title="Libro completado", media_type=MediaType.LIBRO, status=MediaStatus.COMPLETADO)
        crear_item(title="Peli completada", media_type=MediaType.PELICULA, status=MediaStatus.COMPLETADO)
        crear_item(title="Libro pendiente", media_type=MediaType.LIBRO, status=MediaStatus.PENDIENTE)

        html = client.get(f"/listas/{listas_dinamicas['completado'].id}").text
        assert "Libro completado" in html
        assert "Peli completada" in html
        assert "Libro pendiente" not in html

    def test_se_actualiza_sola_cuando_cambia_el_estado_del_item(self, client, crear_item, listas_dinamicas):
        item = crear_item(title="En curso", status=MediaStatus.PENDIENTE)

        antes = client.get(f"/listas/{listas_dinamicas['pendiente'].id}").text
        assert "En curso" in antes

        client.post(f"/item/{item.id}/actualizar", data={"title": item.title, "status": "completado"})

        pendientes_despues = client.get(f"/listas/{listas_dinamicas['pendiente'].id}").text
        completados_despues = client.get(f"/listas/{listas_dinamicas['completado'].id}").text
        assert "En curso" not in pendientes_despues
        assert "En curso" in completados_despues

    def test_no_se_puede_eliminar(self, client, listas_dinamicas):
        lista_id = listas_dinamicas["wishlist"].id
        client.post(f"/listas/{lista_id}/eliminar", follow_redirects=False)
        # Sigue existiendo (una lista manual, en cambio, desaparecería)
        assert client.get(f"/listas/{lista_id}").status_code == 200
        assert "Wishlist" in client.get(f"/listas/{lista_id}").text

    def test_no_admite_anadir_items_a_mano(self, client, crear_item, listas_dinamicas):
        item = crear_item(title="Suelto", status=MediaStatus.PENDIENTE)
        lista_id = listas_dinamicas["completado"].id

        client.post(f"/item/{item.id}/anadir-lista", data={"list_id": str(lista_id)})

        assert "Suelto" not in client.get(f"/listas/{lista_id}").text

    def test_no_aparece_en_el_desplegable_de_anadir_a_lista(self, client, crear_item, listas_dinamicas):
        item = crear_item(title="Cualquiera")
        html = client.get(f"/item/{item.id}").text
        assert "Completados" not in html


class TestPaginaDeListas:
    def test_separa_automaticas_de_manuales(self, client, listas_dinamicas):
        client.post("/listas", data={"name": "Para ver con pareja"})
        html = client.get("/listas").text
        assert "Vistas automáticas" in html
        assert "Tus listas" in html
        assert "Para ver con pareja" in html

    def test_el_contador_de_una_vista_automatica_es_en_vivo(self, client, crear_item, listas_dinamicas):
        crear_item(title="Uno", status=MediaStatus.WISHLIST)
        crear_item(title="Dos", status=MediaStatus.WISHLIST)
        html = client.get("/listas").text
        assert "2 ítems" in html


class TestInicioEnlazaAVistasReales:
    def test_los_4_accesos_apuntan_a_una_lista_real(self, client, listas_dinamicas):
        html = client.get("/").text
        for estado in ("en_progreso", "pendiente", "completado", "wishlist"):
            assert f'/listas/{listas_dinamicas[estado].id}"' in html
