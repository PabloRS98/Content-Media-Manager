"""Tests de `enrich_missing_covers` más allá de lo que ya cubre
test_fallos_conocidos.py: el backfill de duración/reparto para películas y
series importadas de IMDb (hallazgo N2, encontrado auditando datos reales,
no en la auditoría original).

`metadata.enrich_item` solo actúa sobre ítems con `external_source == "tmdb"`,
pero el importador de IMDb los crea con `external_source == "imdb"` y nunca
llama a `enrich_item`. Antes de este fix, un ítem así podía conseguir portada
vía `enrich_missing_covers` (que sí busca en TMDB) sin conseguir nunca su
duración: la búsqueda por lote encontraba y guardaba `cover_url`, pero no
promovía el ítem a `external_source="tmdb"`, así que `enrich_item` seguía sin
poder actuar sobre él en ninguna ejecución futura.
"""
from app.models import MediaItem, MediaStatus, MediaType
from app.services import enrich


def _detalles_tmdb_falsos(**overrides):
    base = {
        "title": "Blade Runner",
        "runtime_minutes": 117,
        "cast": "Harrison Ford, Rutger Hauer",
        "creator": "Ridley Scott",
        "genres": "Ciencia ficción",
        "overview": "Un blade runner persigue replicantes.",
        "tmdb_collection_id": None,
        "saga": None,
        "release_date": "1982-06-25",
    }
    base.update(overrides)
    return base


class TestBackfillDeDuracionParaImportsDeImdb:
    def test_una_pelicula_de_imdb_recibe_duracion_al_encontrar_portada(self, usuario, db, monkeypatch):
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich.tmdb, "search_movies", lambda *a, **k: [{
            "external_id": "78", "title": "Blade Runner", "cover_url": "https://ejemplo/portada.jpg",
            "overview": "", "genres": None, "year": 1982,
        }])
        monkeypatch.setattr("app.services.metadata.tmdb.get_movie_details",
                            lambda tmdb_id: _detalles_tmdb_falsos())

        item = MediaItem(usuario_id=usuario.id, media_type=MediaType.PELICULA, title="Blade Runner",
                          external_source="imdb", external_id="imdb:tt0083658",
                          status=MediaStatus.COMPLETADO)
        db.add(item)
        db.commit()

        enrich.enrich_missing_covers(db)

        db.refresh(item)
        assert item.runtime_minutes == 117
        assert item.cast == "Harrison Ford, Rutger Hauer"
        assert item.external_source == "tmdb"
        assert item.external_id == "78"

    def test_una_serie_de_imdb_recibe_episodios_al_encontrar_portada(self, usuario, db, monkeypatch):
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich.tmdb, "search_tv", lambda *a, **k: [{
            "external_id": "1396", "title": "Breaking Bad", "cover_url": "https://ejemplo/portada.jpg",
            "overview": "", "genres": None, "year": 2008,
        }])
        monkeypatch.setattr("app.services.metadata.tmdb.get_tv_details", lambda tmdb_id: {
            "title": "Breaking Bad", "cast": "Bryan Cranston", "creator": "Vince Gilligan",
            "genres": "Drama", "overview": "Un profesor de química.",
            "runtime_minutes": 47, "seasons": [1],
        })
        monkeypatch.setattr("app.services.metadata.tmdb.fetch_tv_episodes", lambda tmdb_id, seasons: [
            {"season_number": 1, "episode_number": 1, "name": "Piloto",
             "overview": "", "air_date": "2008-01-20", "runtime_minutes": 58},
        ])

        item = MediaItem(usuario_id=usuario.id, media_type=MediaType.SERIE, title="Breaking Bad",
                          external_source="imdb", external_id="imdb:tt0903747",
                          status=MediaStatus.COMPLETADO)
        db.add(item)
        db.commit()

        enrich.enrich_missing_covers(db)

        db.refresh(item)
        assert item.runtime_minutes == 47
        assert len(item.episodes) == 1
        assert item.episodes[0].name == "Piloto"

    def test_un_libro_de_imdb_no_se_promociona_a_tmdb(self, usuario, db, monkeypatch):
        """El backfill de duración es solo para pelis/series (TMDB); un libro
        emparejado por Google Books no debe tocar external_source/external_id."""
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich, "_search_for", lambda item: [{
            "title": "Dune", "cover_url": "https://ejemplo/portada.jpg",
            "external_id": "abc123", "overview": "", "genres": None, "year": 1965,
        }])

        item = MediaItem(usuario_id=usuario.id, media_type=MediaType.LIBRO, title="Dune",
                          external_source="goodreads")
        db.add(item)
        db.commit()

        enrich.enrich_missing_covers(db)

        db.refresh(item)
        assert item.external_source == "goodreads"
