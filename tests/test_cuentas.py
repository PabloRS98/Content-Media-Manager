"""Cuentas de la casa: cada una ve lo suyo y nada más.

Este fichero recorre el camino real --pasar por el selector, escribir la
contraseña-- y no usa el atajo del `client` de conftest, que inyecta la cuenta
directamente. Lo que se prueba aquí es precisamente el login y el aislamiento.
"""
import pytest
from fastapi.testclient import TestClient

from app.cuentas import cifrar_password, comprobar_password
from app.database import get_db
from app.main import app
from app.models import Lista, MediaItem, MediaStatus, MediaType, Usuario


@pytest.fixture
def cliente_sin_cuenta(db):
    """Cliente que NO tiene cuenta abierta: entra por el selector como haría
    una persona de verdad."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, headers={"origin": "http://testserver"})
    finally:
        app.dependency_overrides.clear()


def entrar(cliente, usuario, password=""):
    return cliente.post(
        "/cuentas/entrar/%d" % usuario.id, data={"password": password},
        follow_redirects=False,
    )


class TestContrasenas:
    def test_el_hash_no_guarda_la_contrasena(self):
        cifrada = cifrar_password("secreta")
        assert "secreta" not in cifrada
        assert cifrada.startswith("scrypt$")

    def test_la_misma_contrasena_da_hashes_distintos(self):
        """Con sal por cuenta: sin ella bastaría una tabla precalculada."""
        assert cifrar_password("igual") != cifrar_password("igual")

    def test_la_contrasena_correcta_valida(self):
        assert comprobar_password("secreta", cifrar_password("secreta"))

    def test_una_contrasena_incorrecta_no_valida(self):
        assert not comprobar_password("otra", cifrar_password("secreta"))

    def test_sin_hash_nunca_valida(self):
        """Una cuenta sin contraseña no se autentica por aquí: entra directa."""
        assert not comprobar_password("loquesea", None)
        assert not comprobar_password("", None)

    def test_un_hash_corrupto_no_revienta(self):
        assert not comprobar_password("x", "basura")
        assert not comprobar_password("x", "md5$sal$hash")


class TestEntrarYSalir:
    def test_una_cuenta_sin_contrasena_entra_de_un_clic(self, cliente_sin_cuenta, usuario):
        r = entrar(cliente_sin_cuenta, usuario)
        assert r.status_code == 303
        assert cliente_sin_cuenta.get("/").status_code == 200

    def test_una_cuenta_con_contrasena_no_entra_sin_ella(self, cliente_sin_cuenta, db, usuario):
        """El punto entero de tener contraseña: elegirla en el selector no basta."""
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()

        entrar(cliente_sin_cuenta, usuario)
        assert cliente_sin_cuenta.get("/", follow_redirects=False).status_code == 303

    def test_una_cuenta_con_contrasena_entra_con_ella(self, cliente_sin_cuenta, db, usuario):
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()

        entrar(cliente_sin_cuenta, usuario, "micontrasena")
        assert cliente_sin_cuenta.get("/").status_code == 200

    def test_la_contrasena_equivocada_no_entra(self, cliente_sin_cuenta, db, usuario):
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()

        entrar(cliente_sin_cuenta, usuario, "otra")
        assert cliente_sin_cuenta.get("/", follow_redirects=False).status_code == 303

    def test_sin_cuenta_abierta_todo_lleva_al_selector(self, cliente_sin_cuenta):
        for ruta in ("/", "/catalogo", "/listas", "/estadisticas", "/estado"):
            r = cliente_sin_cuenta.get(ruta, follow_redirects=False)
            assert r.status_code == 303, ruta
            assert r.headers["location"] == "/cuentas"

    def test_el_selector_se_ve_sin_cuenta_abierta(self, cliente_sin_cuenta, usuario):
        """Si pidiera cuenta, sería un bucle."""
        r = cliente_sin_cuenta.get("/cuentas")
        assert r.status_code == 200
        assert usuario.nombre in r.text

    def test_salir_cierra_la_sesion(self, cliente_sin_cuenta, usuario):
        entrar(cliente_sin_cuenta, usuario)
        cliente_sin_cuenta.post("/cuentas/salir", follow_redirects=False)
        assert cliente_sin_cuenta.get("/", follow_redirects=False).status_code == 303


class TestElSelectorNoFiltraNada:
    def test_no_enseña_el_catalogo_de_nadie(self, cliente_sin_cuenta, db, usuario, crear_item):
        crear_item(title="Un libro muy privado")
        html = cliente_sin_cuenta.get("/cuentas").text
        assert "Un libro muy privado" not in html

    def test_no_enseña_el_hash_de_la_contrasena(self, cliente_sin_cuenta, db, usuario):
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()
        html = cliente_sin_cuenta.get("/cuentas").text
        assert "scrypt$" not in html
        assert "micontrasena" not in html


class TestAislamientoEntreCuentas:
    """Lo que de verdad hay que demostrar: dos personas de la misma casa no ven
    ni tocan lo del otro."""

    @pytest.fixture
    def item_ajeno(self, db, otro_usuario):
        item = MediaItem(
            usuario_id=otro_usuario.id, title="Diario de la otra persona",
            media_type=MediaType.LIBRO, status=MediaStatus.PENDIENTE,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_el_catalogo_no_muestra_items_ajenos(self, client, crear_item, item_ajeno):
        crear_item(title="Lo mío")
        html = client.get("/catalogo").text
        assert "Lo mío" in html
        assert "Diario de la otra persona" not in html

    def test_la_portada_no_muestra_items_ajenos(self, client, item_ajeno):
        assert "Diario de la otra persona" not in client.get("/").text

    def test_la_busqueda_no_encuentra_items_ajenos(self, client, item_ajeno):
        html = client.get("/catalogo", params={"buscar": "diario"}).text
        assert "Diario de la otra persona" not in html

    def test_no_se_puede_abrir_la_ficha_de_un_item_ajeno(self, client, item_ajeno):
        """Los ids son globales: pedir /item/N desde otra cuenta traería el
        ítem de quien sea si no se comprobara el dueño."""
        r = client.get("/item/%d" % item_ajeno.id, follow_redirects=False)
        assert r.status_code == 303  # redirige, no lo enseña

    def test_no_se_puede_editar_un_item_ajeno(self, client, db, item_ajeno):
        client.post("/item/%d/actualizar" % item_ajeno.id,
                    data={"title": "Secuestrado", "status": "completado"},
                    follow_redirects=False)
        db.refresh(item_ajeno)
        assert item_ajeno.title == "Diario de la otra persona"

    def test_no_se_puede_borrar_un_item_ajeno(self, client, db, item_ajeno):
        client.post("/item/%d/eliminar" % item_ajeno.id, follow_redirects=False)
        assert db.get(MediaItem, item_ajeno.id) is not None

    def test_las_estadisticas_solo_cuentan_lo_propio(self, client, crear_item, item_ajeno):
        crear_item(title="Mío", status=MediaStatus.COMPLETADO)
        html = client.get("/estadisticas").text
        assert "Diario de la otra persona" not in html

    def test_las_listas_son_de_cada_uno(self, client, db, usuario, otro_usuario):
        db.add(Lista(usuario_id=otro_usuario.id, name="Lista ajena"))
        db.commit()
        assert "Lista ajena" not in client.get("/listas").text

    def test_no_se_puede_abrir_una_lista_ajena(self, client, db, otro_usuario):
        ajena = Lista(usuario_id=otro_usuario.id, name="Lista ajena")
        db.add(ajena)
        db.commit()
        db.refresh(ajena)
        r = client.get("/listas/%d" % ajena.id, follow_redirects=False)
        assert r.status_code == 303

    def test_dos_cuentas_pueden_tener_una_lista_con_el_mismo_nombre(
        self, client, db, usuario, otro_usuario
    ):
        """El `unique` era global: eso habría impedido que cada uno tuviera su
        propia lista "Pendientes"."""
        db.add(Lista(usuario_id=otro_usuario.id, name="Para el finde"))
        db.commit()

        client.post("/listas", data={"name": "Para el finde"}, follow_redirects=False)

        assert db.query(Lista).filter(Lista.name == "Para el finde").count() == 2


class TestCrearYGestionarCuentas:
    def test_se_puede_crear_una_cuenta_sin_contrasena(self, cliente_sin_cuenta, db):
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Invitado"},
                                follow_redirects=False)
        creada = db.query(Usuario).filter(Usuario.nombre == "Invitado").one()
        assert creada.password_hash is None

    def test_una_cuenta_nueva_recibe_sus_vistas_automaticas(self, cliente_sin_cuenta, db):
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Invitado"},
                                follow_redirects=False)
        creada = db.query(Usuario).filter(Usuario.nombre == "Invitado").one()
        suyas = db.query(Lista).filter(
            Lista.usuario_id == creada.id, Lista.filtro_estado.isnot(None)
        ).count()
        assert suyas == 4

    def test_una_cuenta_nueva_nace_con_el_catalogo_vacio(self, cliente_sin_cuenta, db,
                                                         usuario, crear_item):
        crear_item(title="Lo de la primera cuenta")
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Invitado"},
                                follow_redirects=False)
        creada = db.query(Usuario).filter(Usuario.nombre == "Invitado").one()

        entrar(cliente_sin_cuenta, creada)
        assert "Lo de la primera cuenta" not in cliente_sin_cuenta.get("/catalogo").text

    def test_no_se_repiten_los_nombres(self, cliente_sin_cuenta, db, usuario):
        cliente_sin_cuenta.post("/cuentas", data={"nombre": usuario.nombre},
                                follow_redirects=False)
        assert db.query(Usuario).filter(Usuario.nombre == usuario.nombre).count() == 1

    def test_una_contrasena_demasiado_corta_se_rechaza(self, cliente_sin_cuenta, db):
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Corto", "password": "ab"},
                                follow_redirects=False)
        assert db.query(Usuario).filter(Usuario.nombre == "Corto").first() is None

    def test_se_puede_poner_contrasena_a_una_cuenta_que_no_la_tenia(
        self, cliente_sin_cuenta, db, usuario
    ):
        entrar(cliente_sin_cuenta, usuario)
        cliente_sin_cuenta.post("/cuentas/ajustes/password",
                                data={"nueva": "micontrasena"}, follow_redirects=False)
        db.refresh(usuario)
        assert usuario.tiene_password

    def test_para_quitar_la_contrasena_hay_que_saberla(self, cliente_sin_cuenta, db, usuario):
        """Si no, quien pillara la sesión abierta podría dejar la cuenta sin
        protección."""
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()
        entrar(cliente_sin_cuenta, usuario, "micontrasena")

        cliente_sin_cuenta.post("/cuentas/ajustes/password",
                                data={"actual": "equivocada", "nueva": ""},
                                follow_redirects=False)
        db.refresh(usuario)
        assert usuario.tiene_password

    def test_con_la_contrasena_actual_si_se_puede_quitar(self, cliente_sin_cuenta, db, usuario):
        usuario.password_hash = cifrar_password("micontrasena")
        db.commit()
        entrar(cliente_sin_cuenta, usuario, "micontrasena")

        cliente_sin_cuenta.post("/cuentas/ajustes/password",
                                data={"actual": "micontrasena", "nueva": ""},
                                follow_redirects=False)
        db.refresh(usuario)
        assert not usuario.tiene_password

    def test_se_puede_cambiar_el_nombre(self, cliente_sin_cuenta, db, usuario):
        entrar(cliente_sin_cuenta, usuario)
        cliente_sin_cuenta.post("/cuentas/ajustes/nombre", data={"nombre": "Otro nombre"},
                                follow_redirects=False)
        db.refresh(usuario)
        assert usuario.nombre == "Otro nombre"


def test_la_barra_superior_dice_quien_esta_dentro(cliente_sin_cuenta, usuario):
    entrar(cliente_sin_cuenta, usuario)
    assert usuario.nombre in cliente_sin_cuenta.get("/").text
