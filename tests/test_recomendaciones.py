"""Recomendaciones desde el propio catálogo, sin APIs ni modelos.

La app ya sabe qué has completado, con qué nota, de qué sagas, creadores y
géneros. Con eso se ordena lo pendiente por afinidad y --lo que de verdad
importa-- se dice **por qué**.
"""
import pytest

from app.models import MediaStatus, MediaType, Priority
from app.services.recomendaciones import recomendar


@pytest.fixture
def completar(crear_item):
    def _crear(titulo, **campos):
        campos.setdefault("status", MediaStatus.COMPLETADO)
        return crear_item(title=titulo, **campos)
    return _crear


@pytest.fixture
def pendiente(crear_item):
    def _crear(titulo, **campos):
        campos.setdefault("status", MediaStatus.PENDIENTE)
        return crear_item(title=titulo, **campos)
    return _crear


def titulos(recomendaciones):
    return [r.item.title for r in recomendaciones]


class TestDeDondeSaleLaAfinidad:
    def test_recomienda_por_saga(self, db, completar, pendiente):
        completar("El imperio final", saga="Nacidos de la bruma", rating=9)
        pendiente("El pozo de la ascensión", saga="Nacidos de la bruma")
        pendiente("Otro cualquiera")

        recs = recomendar(db)
        assert titulos(recs) == ["El pozo de la ascensión"]
        assert "El imperio final" in recs[0].motivos[0]

    def test_recomienda_por_creador(self, db, completar, pendiente):
        completar("Dune", creator="Frank Herbert", rating=10)
        pendiente("Mesías de Dune", creator="Frank Herbert")
        pendiente("Nada que ver", creator="Otra persona")

        assert titulos(recomendar(db)) == ["Mesías de Dune"]

    def test_recomienda_por_genero(self, db, completar, pendiente):
        completar("Peli 1", genres="Ciencia ficción", media_type=MediaType.PELICULA)
        completar("Peli 2", genres="Ciencia ficción", media_type=MediaType.PELICULA)
        pendiente("Peli 3", genres="Ciencia ficción", media_type=MediaType.PELICULA)
        pendiente("Peli 4", genres="Comedia romántica", media_type=MediaType.PELICULA)

        assert titulos(recomendar(db)) == ["Peli 3"]

    def test_la_wishlist_tambien_entra(self, db, completar, crear_item):
        completar("Dune", creator="Frank Herbert", rating=9)
        crear_item(title="Mesías de Dune", creator="Frank Herbert",
                   status=MediaStatus.WISHLIST)

        assert titulos(recomendar(db)) == ["Mesías de Dune"]


class TestElOrden:
    def test_la_saga_pesa_mas_que_el_creador(self, db, completar, pendiente):
        """Que te gustara un libro de una saga dice más del siguiente de esa
        saga que del siguiente libro del mismo autor."""
        completar("Base", saga="Mi saga", creator="Autora", rating=9)
        pendiente("Por saga", saga="Mi saga", creator="Otra persona")
        pendiente("Por autora", saga=None, creator="Autora")

        assert titulos(recomendar(db))[0] == "Por saga"

    def test_una_nota_alta_pesa_mas_que_solo_haberlo_terminado(self, db, completar, pendiente):
        completar("Me encantó", creator="Autora A", rating=10)
        completar("Lo terminé", creator="Autora B", rating=None)
        pendiente("De la que me encantó", creator="Autora A")
        pendiente("De la que solo terminé", creator="Autora B")

        assert titulos(recomendar(db))[0] == "De la que me encantó"

    def test_la_prioridad_alta_suma(self, db, completar, pendiente):
        """Una prioridad puesta a mano es una señal explícita del usuario."""
        completar("Base", creator="Autora", rating=9)
        pendiente("Normal", creator="Autora")
        pendiente("Prioritario", creator="Autora", priority=Priority.ALTA)

        assert titulos(recomendar(db))[0] == "Prioritario"

    def test_se_respeta_el_limite(self, db, completar, pendiente):
        completar("Base", genres="Fantasía", rating=9)
        for n in range(10):
            pendiente("Pendiente %02d" % n, genres="Fantasía")

        assert len(recomendar(db, limite=3)) == 3


class TestElPorque:
    def test_cada_recomendacion_dice_su_motivo(self, db, completar, pendiente):
        completar("El imperio final", saga="Nacidos de la bruma", rating=9)
        pendiente("El pozo de la ascensión", saga="Nacidos de la bruma")

        rec = recomendar(db)[0]
        assert rec.motivos
        assert all(m.strip() for m in rec.motivos)

    def test_un_item_con_varias_afinidades_las_acumula(self, db, completar, pendiente):
        completar("Base", saga="Mi saga", creator="Autora", genres="Fantasía", rating=10)
        pendiente("Todo a la vez", saga="Mi saga", creator="Autora", genres="Fantasía")

        rec = recomendar(db)[0]
        assert len(rec.motivos) == 3


class TestCuandoNoHayNadaQueDecir:
    def test_sin_nada_completado_no_recomienda(self, db, pendiente):
        """Mejor no enseñar la sección que inventarse afinidades."""
        pendiente("Solo pendientes")
        assert recomendar(db) == []

    def test_un_catalogo_vacio_no_rompe(self, db):
        assert recomendar(db) == []

    def test_sin_afinidad_no_se_recomienda_nada(self, db, completar, pendiente):
        completar("Un libro", creator="Autora A", genres="Fantasía", rating=9)
        pendiente("Nada que ver", creator="Autora B", genres="Ensayo")

        assert recomendar(db) == []

    def test_no_se_recomienda_lo_ya_completado(self, db, completar):
        completar("Uno", saga="Mi saga", rating=9)
        completar("Dos", saga="Mi saga", rating=9)

        assert recomendar(db) == []

    def test_los_campos_nulos_no_rompen(self, db, completar, pendiente):
        completar("Sin nada", creator=None, saga=None, genres=None, rating=9)
        pendiente("Tampoco", creator=None, saga=None, genres=None)

        assert recomendar(db) == []


class TestEnLaPortada:
    def test_la_seccion_aparece_cuando_hay_recomendaciones(self, client, completar, pendiente):
        completar("El imperio final", saga="Nacidos de la bruma", rating=9)
        pendiente("El pozo de la ascensión", saga="Nacidos de la bruma")

        html = client.get("/").text
        assert "Porque te gustó" in html
        assert "El pozo de la ascensión" in html
        assert "El imperio final" in html  # el motivo

    def test_la_seccion_no_aparece_si_no_hay_de_donde_deducir(self, client, pendiente):
        pendiente("Solo pendientes")
        assert "Porque te gustó" not in client.get("/").text

    def test_la_portada_sigue_respondiendo_con_el_catalogo_vacio(self, client):
        assert client.get("/").status_code == 200
