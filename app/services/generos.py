"""Géneros: de cadena separada por comas a tabla. [N4]

La interfaz y los importadores siguen hablando en texto ("Drama, Comedia"),
porque escribirlos separados por comas es cómodo y es lo que devuelven las seis
APIs de metadatos. Lo que cambia es lo que se guarda: aquí se traduce ese texto
a filas de `generos`, y de vuelta.

El vocabulario se comparte entre ítems --y entre cuentas, igual que las
etiquetas--, así que hay un solo sitio donde normalizar el nombre. Sin eso,
"fantasía", "Fantasía" y " Fantasía " serían tres géneros distintos y el
desplegable del filtro los ofrecería los tres.
"""
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Genero, MediaItem, media_item_generos


def normalizar(nombre: str) -> str:
    """Sin espacios de sobra y con la primera letra en mayúscula.

    `capitalize()` a secas pasaría "Sci-Fi" a "Sci-fi" y "BBC" a "Bbc": solo se
    toca la primera letra, el resto se respeta tal y como lo escribieron.
    """
    limpio = " ".join(nombre.split())
    return limpio[:1].upper() + limpio[1:] if limpio else ""


def desde_texto(texto: str | None) -> list[str]:
    """"Drama, comedia ,, Drama" -> ["Drama", "Comedia"], sin repetidos."""
    vistos: dict[str, None] = {}
    for trozo in (texto or "").split(","):
        nombre = normalizar(trozo)
        if nombre:
            vistos.setdefault(nombre, None)
    return list(vistos)


def texto_de(item: MediaItem) -> str:
    """Lo que se pinta en el campo de edición y en las importaciones."""
    return ", ".join(g.nombre for g in item.generos)


def asignar(db: Session, item: MediaItem, nombres: str | Iterable[str]) -> None:
    """Deja el ítem exactamente con esos géneros, creando los que falten.

    Una sola consulta para resolver todos los nombres, no una por género: es el
    mismo criterio que ya seguían las etiquetas al guardar un ítem.
    """
    if isinstance(nombres, str):
        nombres = desde_texto(nombres)
    else:
        nombres = desde_texto(", ".join(nombres))

    if not nombres:
        item.generos = []
        return

    # Los que ya están en la base, MÁS los que esta misma sesión tiene todavía
    # sin escribir. Lo segundo no es un detalle: la sesión de la app va con
    # `autoflush=False`, así que un `Genero` añadido hace tres filas de un CSV
    # todavía no lo ve ninguna consulta. Sin esto, importar 30 000 películas de
    # "Drama" intentaba crear 30 000 filas "Drama" y el import entero moría con
    # "UNIQUE constraint failed: generos.nombre".
    existentes = {
        g.nombre: g for g in db.query(Genero).filter(Genero.nombre.in_(nombres)).all()
    }
    for pendiente in db.new:
        if isinstance(pendiente, Genero) and pendiente.nombre in nombres:
            existentes.setdefault(pendiente.nombre, pendiente)
    finales = []
    for nombre in nombres:
        genero = existentes.get(nombre)
        if genero is None:
            genero = Genero(nombre=nombre)
            db.add(genero)
            existentes[nombre] = genero
        finales.append(genero)
    item.generos = finales


def añadir_si_faltan(db: Session, item: MediaItem, nombres: str | Iterable[str]) -> None:
    """Como `asignar`, pero sin quitar los que ya tenga.

    Lo usan el enriquecimiento y los metadatos: rellenan lo que falta, y no
    tienen por qué saber más que quien lo escribió a mano.
    """
    if isinstance(nombres, str):
        nombres = desde_texto(nombres)
    actuales = [g.nombre for g in item.generos]
    asignar(db, item, actuales + list(nombres))


def borrar_huerfanos(db: Session) -> int:
    """Borra los géneros que ya no usa ningún ítem. Devuelve cuántos.

    Sin esto la tabla crece para siempre y el desplegable del filtro acaba
    ofreciendo géneros que no tiene nadie. Mismo tratamiento que las etiquetas,
    y por el mismo motivo.
    """
    huerfanos = db.query(Genero).filter(
        ~Genero.id.in_(select(media_item_generos.c.genero_id))
    ).all()
    for genero in huerfanos:
        db.delete(genero)
    if huerfanos:
        db.commit()
    return len(huerfanos)


def con_cuantos_items(db: Session, usuario) -> list[tuple[Genero, int]]:
    """Los géneros del catálogo de esa cuenta, con cuántos ítems suyos tienen.

    Es un GROUP BY, no un recuento por género: con la cadena esto no se podía
    ni plantear.
    """
    return (
        db.query(Genero, func.count(MediaItem.id))
        .join(media_item_generos, Genero.id == media_item_generos.c.genero_id)
        .join(MediaItem, MediaItem.id == media_item_generos.c.media_item_id)
        .filter(MediaItem.usuario_id == usuario.id)
        .group_by(Genero.id)
        .order_by(func.count(MediaItem.id).desc(), Genero.nombre)
        .all()
    )


def renombrar(db: Session, genero: Genero, nombre_nuevo: str) -> str | None:
    """Renombra, y si el nombre ya existe FUSIONA los dos.

    Fusionar es el caso de verdad: "Sci-Fi" y "Ciencia ficción" ya están los dos
    en la base, y lo que uno quiere es que pasen a ser uno solo. Fallar con "ya
    existe" dejaría el problema exactamente donde estaba.

    Devuelve un mensaje de error, o None si fue bien.
    """
    nombre_nuevo = normalizar(nombre_nuevo)
    if not nombre_nuevo:
        return "El género necesita un nombre"
    if nombre_nuevo == genero.nombre:
        return None

    destino = db.query(Genero).filter(Genero.nombre == nombre_nuevo).first()
    if destino is None:
        genero.nombre = nombre_nuevo
        db.commit()
        return None

    for item in list(genero.items):
        if destino not in item.generos:
            item.generos.append(destino)
        item.generos.remove(genero)
    db.delete(genero)
    db.commit()
    return None
