"""Qué de lo que ya tienes pendiente encaja con lo que te ha gustado.

Sin APIs ni modelos: la app ya sabe qué has completado, con qué nota, de qué
sagas, de qué creadores y de qué géneros. Con eso se puede ordenar lo pendiente
por afinidad y, sobre todo, **decir por qué** -- que es lo que convierte el
catálogo en algo que sirve para decidir qué hacer esta noche, y no solo para
registrar lo hecho.

Complementa a `suggest_random`, que ya existía pero es puramente aleatorio.

Deliberadamente NO sale a buscar lo que no tienes. El informe sugería proponer
títulos de la misma saga que aún no estén en el catálogo, pero eso exige
consultar TMDB, y entonces esto deja de ser instantáneo y offline. Se
recomienda entre lo que ya está: es lo que responde a "¿y ahora qué leo?".
"""
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import MediaItem, MediaStatus

# Qué se considera "candidato": lo que aún no has consumido.
CANDIDATOS = (MediaStatus.PENDIENTE, MediaStatus.WISHLIST)

# Qué se considera "te gustó". Un completado sin nota cuenta como señal débil
# (lo terminaste, algo tendría), y una nota alta como señal fuerte.
NOTA_ALTA = 8

# Pesos. La saga pesa más que el creador y el creador más que el género porque
# así de específica es la señal: que te gustara un libro de una saga dice mucho
# más del siguiente de esa saga que del siguiente libro del mismo género.
PESO_SAGA = 5
PESO_CREADOR = 3
PESO_GENERO = 1
# Empujoncito a lo que ya has empezado a mirar: una prioridad alta puesta a
# mano es una señal explícita del usuario y debe ganar a las deducidas.
PESO_PRIORIDAD_ALTA = 2


@dataclass
class Recomendacion:
    item: MediaItem
    puntos: int = 0
    motivos: list[str] = field(default_factory=list)


def _generos_de_cadena(cadena: str | None) -> list[str]:
    """`genres` es una cadena separada por comas, no una relación."""
    if not cadena:
        return []
    return [g.strip().lower() for g in cadena.split(",") if g.strip()]


def _gustos(db: Session) -> tuple[dict, dict, Counter]:
    """Lo que se sabe de los gustos, a partir de lo ya completado.

    Devuelve (sagas, creadores, géneros). Los dos primeros guardan además el
    título que justifica la afinidad, para poder decir "porque te gustó X".

    Se piden solo las cinco columnas que hacen falta, no los objetos enteros:
    esto corre en cada carga de la portada y traerse el catálogo completo --con
    `overview`, que es Text-- es justo lo que quitó MC-M5.
    """
    filas = db.query(
        MediaItem.title, MediaItem.rating, MediaItem.saga,
        MediaItem.creator, MediaItem.genres,
    ).filter(MediaItem.status == MediaStatus.COMPLETADO).all()

    sagas: dict[str, tuple[str, int]] = {}
    creadores: dict[str, tuple[str, int]] = {}
    generos: Counter = Counter()

    for titulo, nota, saga, creador, cadena_generos in filas:
        # Una nota alta pesa el doble que un simple "lo terminé".
        fuerza = 2 if (nota or 0) >= NOTA_ALTA else 1

        if saga:
            clave = saga.strip().lower()
            if fuerza >= sagas.get(clave, ("", 0))[1]:
                sagas[clave] = (titulo, fuerza)
        if creador:
            clave = creador.strip().lower()
            if fuerza >= creadores.get(clave, ("", 0))[1]:
                creadores[clave] = (titulo, fuerza)
        for genero in _generos_de_cadena(cadena_generos):
            generos[genero] += fuerza

    return sagas, creadores, generos


def recomendar(db: Session, limite: int = 6) -> list[Recomendacion]:
    """Pendientes ordenados por afinidad, con el porqué de cada uno.

    Devuelve lista vacía si no hay de dónde deducir nada (catálogo recién
    estrenado, o nada completado todavía): en ese caso la interfaz no promete
    lo que no puede cumplir y enseña la sugerencia aleatoria de siempre.
    """
    sagas, creadores, generos = _gustos(db)
    if not sagas and not creadores and not generos:
        return []

    # Los tres géneros que más completas. Más allá de eso la señal es ruido.
    generos_favoritos = {g for g, _ in generos.most_common(3)}

    # Igual que arriba: solo las columnas que se puntúan. Los objetos enteros
    # se traen al final, y solo los `limite` que se van a pintar.
    candidatos = db.query(
        MediaItem.id, MediaItem.saga, MediaItem.creator,
        MediaItem.genres, MediaItem.priority, MediaItem.updated_at,
    ).filter(MediaItem.status.in_(CANDIDATOS)).all()

    puntuados = []
    for id_item, saga, creador, cadena_generos, prioridad, actualizado in candidatos:
        puntos = 0
        motivos: list[str] = []

        if saga and saga.strip().lower() in sagas:
            titulo, fuerza = sagas[saga.strip().lower()]
            puntos += PESO_SAGA * fuerza
            motivos.append("de la misma saga que «%s»" % titulo)

        if creador and creador.strip().lower() in creadores:
            titulo, fuerza = creadores[creador.strip().lower()]
            puntos += PESO_CREADOR * fuerza
            motivos.append("de %s, como «%s»" % (creador, titulo))

        coincidencias = [g for g in _generos_de_cadena(cadena_generos) if g in generos_favoritos]
        if coincidencias:
            puntos += PESO_GENERO * len(coincidencias)
            motivos.append("%s, que es de lo que más terminas" % coincidencias[0].capitalize())

        if prioridad and prioridad.value == "alta":
            puntos += PESO_PRIORIDAD_ALTA
            motivos.append("lo marcaste como prioritario")

        if puntos:
            # Desempate por actividad reciente: entre dos con la misma
            # afinidad, gana el que tocaste hace menos, que es del que te
            # acuerdas.
            puntuados.append((puntos, actualizado, id_item, motivos))

    if not puntuados:
        return []

    puntuados.sort(key=lambda p: (p[0], p[1]), reverse=True)
    elegidos = puntuados[:limite]

    # El `IN` ya acota el resultado, pero el LIMIT lo deja dicho en el SQL: es
    # lo que distingue "traigo unos pocos por id" de "traigo la tabla", y hay
    # un test de la portada que lo comprueba leyendo las sentencias emitidas.
    items = {
        item.id: item
        for item in db.query(MediaItem).filter(
            MediaItem.id.in_([p[2] for p in elegidos])
        ).limit(len(elegidos)).all()
    }
    return [
        Recomendacion(item=items[id_item], puntos=puntos, motivos=motivos)
        for puntos, _, id_item, motivos in elegidos
        if id_item in items
    ]
