"""La comprobación de origen falla cerrada y acepta `Referer` como respaldo.

`test_seguridad.py` cubre lo que ya se decidió en la auditoría anterior. Aquí
va lo que cambia con [MC-A6]: antes, una petición sin `Sec-Fetch-Site` ni
`Origin` se dejaba pasar a propósito. Ahora se rechaza, porque un navegador
manda siempre al menos una de las tres en un POST.
"""
import pytest

SIN_CABECERAS = {"sec-fetch-site": None, "origin": None, "referer": None}


def limpiar(client):
    """Quita las cabeceras de origen que el fixture pone por defecto.

    El `client` de conftest manda `Origin` como haría un navegador; para probar
    el caso "no llega ninguna" hay que retirarlo explícitamente.
    """
    for cabecera in ("origin", "referer", "sec-fetch-site"):
        client.headers.pop(cabecera, None)
    return client


class TestFalloCerrado:
    def test_post_sin_ninguna_cabecera_de_origen_es_rechazado(self, client, crear_item):
        """El cambio de criterio de [MC-A6].

        Antes se dejaba pasar "para no romper clientes que no son navegadores".
        Pero un navegador manda siempre `Origin` o `Referer` en un POST, así que
        el fallo abierto solo beneficiaba a curl y a scripts -- que pueden
        añadir la cabecera con una línea -- mientras dejaba una puerta abierta
        a cualquier webview antiguo que no mande ninguna de las dos.
        """
        item = crear_item()
        r = limpiar(client).post("/item/%d/eliminar" % item.id, follow_redirects=False)
        assert r.status_code == 403

    def test_los_get_siguen_pasando_sin_cabeceras(self, client):
        """Solo se comprueban los métodos que escriben."""
        assert limpiar(client).get("/catalogo").status_code == 200


class TestRespaldoConReferer:
    def test_post_con_referer_del_mismo_origen_se_acepta(self, client, crear_item):
        """Cubre los navegadores viejos que no mandan `Sec-Fetch-Site` ni
        `Origin`. `media-catalog` no miraba `Referer` en absoluto."""
        item = crear_item()
        r = limpiar(client).post(
            "/item/%d/eliminar" % item.id,
            headers={"referer": "http://testserver/item/%d" % item.id},
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_post_con_referer_de_otro_origen_es_rechazado(self, client, crear_item):
        item = crear_item()
        r = limpiar(client).post(
            "/item/%d/eliminar" % item.id,
            headers={"referer": "https://sitio-malicioso.example/pagina"},
            follow_redirects=False,
        )
        assert r.status_code == 403

    def test_origin_manda_sobre_referer(self, client, crear_item):
        """Si llegan las dos, `Origin` es la fiable: `Referer` puede venir
        recortado por la política de referencia del navegador."""
        item = crear_item()
        r = limpiar(client).post(
            "/item/%d/eliminar" % item.id,
            headers={
                "origin": "https://sitio-malicioso.example",
                "referer": "http://testserver/item/1",
            },
            follow_redirects=False,
        )
        assert r.status_code == 403


class TestOrigenesDeConfianza:
    """Hace falta si se llega a la app por un nombre distinto al del proxy."""

    def test_un_origen_de_confianza_se_acepta(self, client, crear_item, monkeypatch):
        from app import csrf

        item = crear_item()
        monkeypatch.setattr(csrf, "_hosts_fiables", lambda: {"catalogo.casa"})
        r = limpiar(client).post(
            "/item/%d/eliminar" % item.id,
            headers={"origin": "https://catalogo.casa"},
            follow_redirects=False,
        )
        assert r.status_code == 303

    def test_un_origen_no_configurado_se_rechaza(self, client, crear_item, monkeypatch):
        from app import csrf

        item = crear_item()
        monkeypatch.setattr(csrf, "_hosts_fiables", lambda: {"catalogo.casa"})
        r = limpiar(client).post(
            "/item/%d/eliminar" % item.id,
            headers={"origin": "https://otro.sitio"},
            follow_redirects=False,
        )
        assert r.status_code == 403


@pytest.mark.parametrize("sec_fetch_site, esperado", [
    ("cross-site", 403),
    ("same-origin", 303),
    ("same-site", 303),
    # "none" = el usuario llegó tecleando la URL o desde un marcador.
    ("none", 303),
])
def test_sec_fetch_site_sigue_mandando_cuando_llega(client, crear_item, sec_fetch_site, esperado):
    """La cabecera más fiable sigue teniendo prioridad: es la única que
    distingue "same-site" de "cross-site" sin comparar cadenas a mano."""
    item = crear_item()
    r = limpiar(client).post(
        "/item/%d/eliminar" % item.id,
        headers={"sec-fetch-site": sec_fetch_site},
        follow_redirects=False,
    )
    assert r.status_code == esperado
