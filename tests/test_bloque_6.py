"""Tus datos, claves foráneas, backups y portadas (tercera auditoría, bloque 6)."""
import csv
import io
import json
import os
import sqlite3
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Episode, Lista, MediaItem, MediaStatus, MediaType, Tag, Usuario
from app.services import datos, enrich, scheduler
from app.services._logging_utils import anotar_fallo


@pytest.fixture
def catalogo(db, usuario, crear_item):
    serie = crear_item(title="Serie & co", media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO,
                       generos=["Drama"])
    serie.episodes.append(Episode(season_number=1, episode_number=1, watched=True,
                                  watched_at=date(2024, 1, 2)))
    serie.tags = [Tag(name="favorita")]
    lista = Lista(usuario_id=usuario.id, name="Con la pareja")
    lista.items.append(serie)
    db.add(lista)
    db.commit()
    return serie


class TestExportar:
    """R3-M21."""

    def test_json_lo_trae_todo(self, client, catalogo):
        r = client.get("/cuentas/ajustes/exportar.json")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        d = json.loads(r.text)
        (item,) = d["items"]
        assert item["title"] == "Serie & co"
        assert item["status"] == "en_progreso"
        assert item["generos"] == ["Drama"] and item["etiquetas"] == ["favorita"]
        assert item["episodios"][0]["watched_at"] == "2024-01-02"
        assert {"nombre": "Con la pareja", "items": ["Serie & co"]} in d["listas"]
        assert "usuario_id" not in item and "id" not in item

    def test_csv_se_abre_en_excel(self, client, catalogo):
        r = client.get("/cuentas/ajustes/exportar.csv")
        assert r.text.startswith("﻿")
        filas = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
        assert filas[0]["title"] == "Serie & co"
        assert filas[0]["episodios_vistos"] == "1"

    def test_solo_exporta_lo_de_la_cuenta(self, client, db, catalogo):
        otra = Usuario(nombre="Otra")
        db.add(otra)
        db.commit()
        db.add(MediaItem(usuario_id=otra.id, title="Ajeno", media_type=MediaType.LIBRO,
                         status=MediaStatus.PENDIENTE))
        db.commit()
        assert "Ajeno" not in client.get("/cuentas/ajustes/exportar.json").text


class TestBorrarCuenta:
    """R3-M21 y R3-B9."""

    def test_pide_escribir_el_nombre(self, client, db, usuario, catalogo):
        r = client.post("/cuentas/ajustes/borrar", data={"confirmacion": "otra cosa"},
                        follow_redirects=False)
        assert r.headers["location"] == "/cuentas/ajustes"
        assert db.get(Usuario, usuario.id) is not None

    def test_borra_la_cuenta_y_todo_lo_suyo_y_nada_mas(self, client, db, usuario, catalogo):
        otra = Usuario(nombre="Otra")
        db.add(otra)
        db.commit()
        ajeno = MediaItem(usuario_id=otra.id, title="Ajeno", media_type=MediaType.LIBRO,
                          status=MediaStatus.PENDIENTE)
        db.add(ajeno)
        db.commit()

        r = client.post("/cuentas/ajustes/borrar", data={"confirmacion": "Titular"},
                        follow_redirects=False)
        assert r.headers["location"] == "/cuentas"
        db.expire_all()
        assert db.query(Usuario).all() == [otra]
        assert [i.title for i in db.query(MediaItem).all()] == ["Ajeno"]
        for tabla in ("episodes", "list_items", "media_item_tags", "media_item_generos", "listas"):
            assert db.execute(text("SELECT count(*) FROM %s" % tabla)).scalar() == 0, tabla
        # Vocabulario que nadie usa ya: fuera.
        assert db.execute(text("SELECT count(*) FROM tags")).scalar() == 0

    def test_con_contrasena_hay_que_darla(self, client, db, usuario, catalogo):
        from app.cuentas import cifrar_password
        usuario.password_hash = cifrar_password("secreto-largo")
        db.commit()
        r = client.post("/cuentas/ajustes/borrar",
                        data={"confirmacion": "Titular", "password": "mal"}, follow_redirects=False)
        assert r.headers["location"] == "/cuentas/ajustes"
        assert db.get(Usuario, usuario.id) is not None

    def test_las_fk_estan_activas(self, db):
        """Un ítem de una cuenta que no existe ya no se puede guardar."""
        db.add(MediaItem(usuario_id=999, title="Huérfano", media_type=MediaType.LIBRO,
                         status=MediaStatus.PENDIENTE))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_la_app_activa_las_fk(self):
        from app.database import engine
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


class TestBackup:
    """R3-B5, R3-B15 y la descarga de R3-M21."""

    def test_es_atomico_privado_y_comprobado(self, tmp_path):
        destino = tmp_path / "copia.db"
        scheduler.backup_database(str(destino))
        assert not os.path.exists(str(destino) + ".tmp")
        assert oct(os.stat(destino).st_mode & 0o777) == "0o600"
        with sqlite3.connect(destino) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def test_si_falla_no_pisa_el_bueno(self, tmp_path, monkeypatch):
        destino = tmp_path / "copia.db"
        destino.write_bytes(b"el backup bueno")

        def _revienta(*a, **k):
            raise sqlite3.OperationalError("disco lleno")

        monkeypatch.setattr(scheduler.sqlite3, "connect", _revienta)
        with pytest.raises(sqlite3.OperationalError):
            scheduler.backup_database(str(destino))
        assert destino.read_bytes() == b"el backup bueno"

    def test_con_una_cuenta_se_descarga(self, client):
        r = client.get("/estado/backup")
        assert r.status_code == 200
        assert r.content.startswith(b"SQLite format 3")

    def test_con_varias_cuentas_y_sin_auth_no(self, client, db):
        db.add(Usuario(nombre="Otra"))
        db.commit()
        r = client.get("/estado/backup", follow_redirects=False)
        assert r.status_code == 303


class TestLoteDePortadas:
    """R3-A4 y R3-B14."""

    def _sin_portada(self, crear_item, n):
        return [crear_item(title="Raro %02d" % i, media_type=MediaType.LIBRO) for i in range(n)]

    def test_lo_buscado_sin_suerte_va_al_final(self, db, usuario, crear_item, monkeypatch):
        monkeypatch.setattr(enrich, "BATCH_SIZE", 3)
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        vistos: list[str] = []
        monkeypatch.setattr(enrich, "_search_for", lambda item: vistos.append(item.title) or [])
        self._sin_portada(crear_item, 5)

        enrich.enrich_missing_covers(db, usuario.id)
        enrich.enrich_missing_covers(db, usuario.id)

        assert vistos[:3] == ["Raro 00", "Raro 01", "Raro 02"]
        assert vistos[3:5] == ["Raro 03", "Raro 04"], vistos

    def test_un_fallo_de_api_no_cuenta_como_buscado(self, db, usuario, crear_item, monkeypatch):
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich, "_search_for", lambda item: anotar_fallo() or [])
        (item,) = self._sin_portada(crear_item, 1)
        enrich.enrich_missing_covers(db, usuario.id)
        db.refresh(item)
        assert item.cover_checked_at is None


def test_la_exportacion_no_incluye_marcas_internas():
    assert "cover_checked_at" not in datos.COLUMNAS_DE_ITEM
    assert "notified" not in datos.COLUMNAS_DE_EPISODIO
