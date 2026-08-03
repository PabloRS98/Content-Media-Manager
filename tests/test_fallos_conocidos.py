"""Fallos ya diagnosticados y todavía sin arreglar.

Cada test describe el comportamiento CORRECTO y va marcado con `xfail(strict=True)`.
Es decir: hoy fallan a propósito y el CI sigue en verde, pero en cuanto se
arregle el fallo el test pasará y `strict` hará fallar la construcción, obligando
a quitar la marca. Así la lista no se queda mintiendo: no se puede arreglar algo
y olvidarse de este fichero.

Las referencias (A1, M2...) son las de `docs/AUDITORIA.md`.
"""
from datetime import date

import pytest

from app.models import MediaItem, MediaStatus, MediaType

pytestmark = pytest.mark.fallo_conocido


def test_los_generos_no_pueden_cerrar_la_etiqueta_script(client, crear_item):
    """El género es texto libre y acaba dentro de un <script> en /estadisticas.
    Jinja trae un `tojson` que escapa `<` y `>` justo para esto, pero
    `app/templating.py` lo sustituye por un `json.dumps` crudo."""
    payload = "</script><img src=x onerror=alert(1)>"
    crear_item(title="Con genero raro", genres=payload,
               status=MediaStatus.COMPLETADO, completed_at=date.today())

    assert payload not in client.get("/estadisticas").text


def test_completar_portadas_no_redirige_fuera_del_sitio(client):
    """La cabecera Referer la controla el cliente; usarla como destino de un
    303 es una redirección abierta."""
    r = client.post("/catalogo/completar-portadas",
                    headers={"referer": "https://sitio-malicioso.example/phishing"},
                    follow_redirects=False)

    assert not r.headers.get("location", "").startswith("http")


@pytest.mark.xfail(strict=True, reason="M2: el dedupe consulta la BD y autoflush está desactivado")
def test_el_importador_de_imdb_no_duplica_dentro_del_mismo_csv(client, db):
    """Los exports de IMDb repiten títulos entre 'Ratings' y 'Watchlist'."""
    csv = (
        "Const,Title,Title Type,Year\n"
        "tt0000001,Peli repetida,movie,2001\n"
        "tt0000001,Peli repetida,movie,2001\n"
    )
    client.post("/importar", files={"archivo": ("r.csv", csv, "text/csv")})

    assert db.query(MediaItem).filter(MediaItem.title == "Peli repetida").count() == 1


@pytest.mark.xfail(strict=True, reason="M3: resta el tamaño del lote, no las portadas encontradas")
def test_el_contador_de_portadas_pendientes_dice_la_verdad(db, monkeypatch):
    """Si no se encontró ninguna portada, siguen faltando todas."""
    from app.services import enrich

    monkeypatch.setattr(enrich, "_search_for", lambda item: [])
    monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
    for i in range(5):
        db.add(MediaItem(media_type=MediaType.LIBRO, title=f"Sin portada {i}"))
    db.commit()

    resultado = enrich.enrich_missing_covers(db)

    pendientes_reales = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()
    assert resultado["restantes"] == pendientes_reales


@pytest.mark.xfail(strict=True, reason="M5: add_item no fija completed_at")
def test_dar_de_alta_algo_ya_completado_registra_la_fecha(client, db):
    """Las estadísticas se apoyan en completed_at; sin ella el ítem es invisible
    en 'completados este año', en el gráfico por meses y en la actividad reciente."""
    client.post("/agregar", data={
        "media_type": "pelicula", "title": "Vista hace tiempo", "status": "completado",
    }, follow_redirects=False)

    item = db.query(MediaItem).filter(MediaItem.title == "Vista hace tiempo").one()
    assert item.completed_at is not None


@pytest.mark.xfail(strict=True, reason="B1: no se escapan los comodines de LIKE")
def test_el_filtro_de_genero_trata_el_porcentaje_como_texto(client, crear_item):
    """`%` es un comodín de LIKE: sin escapar, filtrar por '%' devuelve todo."""
    crear_item(title="Novela negra", genres="Novela negra")

    html = client.get("/catalogo?tipo=libro&genero=%25").text
    assert "Novela negra" not in html


@pytest.mark.xfail(strict=True, reason="B2: _get() se queda con la primera columna presente aunque esté vacía")
def test_el_lector_de_columnas_de_imdb_se_salta_las_vacias():
    """Un CSV bilingüe trae 'Title' y 'Título'. La versión de
    `services/imports.py` sí lo hace bien: son dos copias divergentes."""
    from app.routers.imdb_import import _get

    assert _get({"Title": "", "Título": "Duna"}, "Title", "Título") == "Duna"


@pytest.mark.xfail(strict=True, reason="B4: compare_digest sobre str exige ASCII")
def test_se_puede_usar_una_contrasena_con_tildes(client, monkeypatch):
    """El .env.example está en español, así que es una trampa fácil de pisar."""
    from app import auth
    from app.config import Settings

    monkeypatch.setattr(auth, "settings", Settings(
        enable_auth=True, auth_username="admin", auth_password="contraseña",
    ))

    r = client.get("/catalogo?tipo=libro", auth=("admin", "contraseña"))
    assert r.status_code == 200


@pytest.mark.xfail(strict=True, reason="B5: no se valida el rango de la nota")
def test_no_se_admite_una_nota_fuera_de_1_10(client, crear_item, db):
    """El min/max del HTML es solo del lado del cliente. Una nota de 99 se
    guarda y luego desaparece del histograma sin avisar."""
    item = crear_item()

    client.post(f"/item/{item.id}/actualizar", data={
        "title": item.title, "status": "completado", "priority": "media", "rating": "99",
    }, follow_redirects=False)

    db.refresh(item)
    assert item.rating != 99


@pytest.mark.xfail(strict=True, reason="B6: el contador ignora el filtro de tipo activo")
def test_el_contador_de_portadas_respeta_el_tipo_que_estas_viendo(client, crear_item):
    """Estando en Películas no tiene sentido ver un número que cuenta libros."""
    crear_item(title="Libro sin portada", media_type=MediaType.LIBRO, cover_url=None)
    crear_item(title="Peli con portada", media_type=MediaType.PELICULA,
               cover_url="https://ejemplo/p.jpg")

    html = client.get("/catalogo?tipo=pelicula").text
    assert "Buscar portadas" not in html


@pytest.mark.xfail(strict=True, reason="N1: el enriquecedor renombra el ítem con el título de la API")
def test_enriquecer_una_portada_no_cambia_el_titulo(db, monkeypatch):
    """`_pick_match` acepta por subcadena, así que 'Harry Potter y el cáliz de
    fuego' casa con un volumen genérico 'Harry Potter' y el ítem acaba
    renombrado. Con varios libros de la misma saga quedan filas indistinguibles."""
    from app.services import enrich

    monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
    monkeypatch.setattr(enrich, "_search_for", lambda item: [{
        "title": "Harry Potter",
        "cover_url": "https://ejemplo/portada.jpg",
    }])
    libro = MediaItem(media_type=MediaType.LIBRO,
                      title="Harry Potter y el cáliz de fuego")
    db.add(libro)
    db.commit()

    enrich.enrich_missing_covers(db)

    db.refresh(libro)
    assert libro.title == "Harry Potter y el cáliz de fuego"


@pytest.mark.xfail(strict=True, reason="N3: falta la etiqueta de wishlist en status_labels")
def test_el_desplegable_de_estado_no_muestra_none(client):
    """`statuses` incluye WISHLIST pero ninguno de los cinco diccionarios de
    etiquetas la define, así que Jinja imprime literalmente 'None'."""
    html = client.get("/catalogo?tipo=libro").text
    assert ">None<" not in html


@pytest.mark.xfail(strict=True, reason="N4: los especiales (temporada 0) no se filtran")
def test_los_especiales_no_aparecen_en_proximamente(client, crear_serie, db):
    """TMDB usa la temporada 0 para especiales y resúmenes, que se estrenan el
    mismo día que el episodio real y lo duplican en la lista."""
    from datetime import timedelta

    from app.models import Episode
    serie = crear_serie(temporadas=1, por_temporada=1, status=MediaStatus.EN_PROGRESO)
    cuando = date.today() + timedelta(days=7)
    serie.episodes.append(Episode(season_number=3, episode_number=7, air_date=cuando))
    serie.episodes.append(Episode(season_number=0, episode_number=80, air_date=cuando))
    db.commit()

    html = client.get("/").text
    assert "S03E07" in html
    assert "S00E80" not in html


@pytest.mark.xfail(strict=True, reason="M4: solo se capturan los HTTPStatusError, no los de transporte")
def test_una_caida_de_google_books_no_devuelve_un_500(client, monkeypatch):
    """TMDB, RAWG y Open Library envuelven todo y devuelven lista vacía; Google
    Books es la excepción, y además corta la cascada hacia Open Library."""
    import httpx

    from app.services import googlebooks

    def _caida(*args, **kwargs):
        raise httpx.ConnectError("red caída")

    monkeypatch.setattr(googlebooks.httpx, "get", _caida)

    assert client.get("/buscar?tipo=libro&q=dune").status_code == 200


@pytest.mark.xfail(strict=True, reason="B3: existing[:-0] es la lista vacía")
def test_backup_keep_a_cero_borra_los_backups_antiguos(tmp_path, monkeypatch):
    """Con backup_keep=0 la intención es no conservar nada, pero el slice
    negativo con 0 devuelve lista vacía y no se borra ninguno."""
    from app.services import scheduler

    backups = tmp_path / "backups"
    backups.mkdir()
    origen = tmp_path / "media.db"
    import sqlite3
    sqlite3.connect(origen).close()
    for dia in ("20200101", "20200102", "20200103"):
        (backups / f"media-{dia}.db").write_bytes(b"")

    monkeypatch.setattr(scheduler.settings, "db_path", str(origen))
    monkeypatch.setattr(scheduler.settings, "backup_keep", 0)
    scheduler.backup_database()

    assert len(list(backups.glob("media-*.db"))) <= 1
