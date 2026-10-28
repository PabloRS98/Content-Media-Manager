"""Parseo del RSS de un podcast: el XML viene de un tercero. [R3-B26]"""
import pytest

from app.services import itunes

FEED_BUENO = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Dos</title><pubDate>Tue, 02 Jan 2024 10:00:00 +0000</pubDate></item>
  <item><title>Uno</title><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate></item>
</channel></rss>"""

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<rss><channel><item><title>&lol2;</title></item></channel></rss>"""


class _Respuesta:
    is_redirect = False

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setattr(itunes, "_es_url_publica", lambda url: True)

    def _servir(contenido):
        monkeypatch.setattr(itunes.httpx, "get", lambda *a, **k: _Respuesta(contenido))
        return itunes.fetch_podcast_episodes("https://ejemplo.com/feed.xml")
    return _servir


def test_un_feed_normal_se_parsea(feed):
    assert [e["name"] for e in feed(FEED_BUENO)] == ["Uno", "Dos"]


def test_un_feed_con_doctype_o_entity_se_rechaza(feed):
    assert feed(BILLION_LAUGHS) == []
    assert feed(b'<!doctype x><rss><channel/></rss>') == []


def test_el_doctype_en_utf16_tambien_se_rechaza(feed):
    assert feed('<!DOCTYPE x><rss><channel/></rss>'.encode("utf-16")) == []


def test_un_feed_desmesurado_se_rechaza(feed):
    relleno = b"<!-- " + b"x" * itunes.MAX_BYTES_FEED + b" -->"
    assert feed(b"<rss><channel>" + relleno + b"</channel></rss>") == []
