"""`cover_url` llega por formulario y acaba en el `src` de un `<img>`.

Hoy no es explotable: Jinja escapa el HTML y `javascript:` no ejecuta en el
`src` de una imagen. Lo que se cierra aquí es la categoría, no un exploit —
el día que ese campo se use en un `<a href>`, el sitio donde se guarda es este.
"""
import pytest

from app.models import MediaItem, MediaType
from app.security import safe_external_url

URLS_RECHAZADAS = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)  ",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "no-es-una-url",
    "/ruta/relativa.png",
    "https://",          # esquema válido, sin host
    "",
    "   ",
]

URLS_ACEPTADAS = [
    "https://image.tmdb.org/t/p/w500/abc.jpg",
    "http://covers.openlibrary.org/b/id/123-L.jpg",
    "https://books.google.com/books/content?id=x&printsec=frontcover",
]


@pytest.mark.parametrize("url", URLS_RECHAZADAS)
def test_safe_external_url_rechaza(url):
    assert safe_external_url(url) is None


@pytest.mark.parametrize("url", URLS_ACEPTADAS)
def test_safe_external_url_acepta(url):
    assert safe_external_url(url) == url


def test_safe_external_url_acepta_none():
    assert safe_external_url(None) is None


class TestAltaDeItem:
    def test_cover_url_con_javascript_no_se_guarda(self, client, db):
        r = client.post("/agregar", data={
            "media_type": "libro", "title": "Con portada rara",
            "cover_url": "javascript:alert(1)",
        }, follow_redirects=False)
        assert r.status_code in (200, 303)

        item = db.query(MediaItem).filter(MediaItem.title == "Con portada rara").one()
        assert item.cover_url is None

    def test_cover_url_https_se_guarda(self, client, db):
        url = "https://image.tmdb.org/t/p/w500/ok.jpg"
        client.post("/agregar", data={
            "media_type": "libro", "title": "Con portada buena", "cover_url": url,
        }, follow_redirects=False)

        item = db.query(MediaItem).filter(MediaItem.title == "Con portada buena").one()
        assert item.cover_url == url


class TestEdicionDeItem:
    def test_cover_url_con_javascript_no_se_guarda(self, client, db, crear_item):
        item = crear_item(title="Editable", media_type=MediaType.LIBRO)
        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Editable", "status": "pendiente",
            "cover_url": "javascript:alert(1)",
        }, follow_redirects=False)

        db.refresh(item)
        assert item.cover_url is None

    def test_una_portada_valida_no_se_pierde_al_editar(self, client, db, crear_item):
        url = "https://image.tmdb.org/t/p/w500/ok.jpg"
        item = crear_item(title="Editable", media_type=MediaType.LIBRO, cover_url=url)
        client.post("/item/%d/actualizar" % item.id, data={
            "title": "Editable", "status": "pendiente", "cover_url": url,
        }, follow_redirects=False)

        db.refresh(item)
        assert item.cover_url == url
