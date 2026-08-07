"""Guardar etiquetas: una sola consulta, y sin dejar huérfanas.

`Tag.name` es `unique=True`, así que cada consulta era rápida -- el problema no
era el coste de cada una, sino que fueran N. Y las etiquetas no se borraban
nunca: al quitar la última "documental" de todos los ítems, la fila seguía ahí
para siempre, esperando a ensuciar una nube de etiquetas o un autocompletado.
"""
import pytest
from sqlalchemy import event

from app.models import Tag


@pytest.fixture
def contador_sql(db):
    motor = db.get_bind()
    lecturas: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        if sentencia.lstrip().upper().startswith("SELECT") and "tags" in sentencia.lower():
            lecturas.append(sentencia.replace("\n", " "))

    event.listen(motor, "before_cursor_execute", _antes)
    try:
        yield lecturas
    finally:
        event.remove(motor, "before_cursor_execute", _antes)


def guardar(client, item, **campos):
    datos = {"title": item.title, "status": item.status.value}
    datos.update(campos)
    return client.post("/item/%d/actualizar" % item.id, data=datos, follow_redirects=False)


def test_guardar_etiquetas_no_consulta_una_vez_por_etiqueta(client, db, crear_item, contador_sql):
    """Lo que importa no es el número exacto de consultas sino que no crezca
    con el número de etiquetas. Quedan tres, todas constantes: la búsqueda de
    las existentes, la carga de la colección que se sustituye, y la limpieza
    de huérfanas."""
    uno = crear_item(title="Con una etiqueta")
    contador_sql.clear()
    guardar(client, uno, tags="una")
    con_una = len(contador_sql)

    otro = crear_item(title="Con ocho etiquetas")
    contador_sql.clear()
    guardar(client, otro, tags="a, b, c, d, e, f, g, h")
    con_ocho = len(contador_sql)

    assert con_ocho == con_una, (
        "%d consultas con 8 etiquetas frente a %d con 1: sigue habiendo un N+1\n%s"
        % (con_ocho, con_una, "\n".join(s[:100] for s in contador_sql))
    )


def test_las_etiquetas_se_guardan(client, db, crear_item):
    item = crear_item(title="Etiquetado")
    guardar(client, item, tags="documental, español")
    db.refresh(item)
    assert {t.name for t in item.tags} == {"documental", "español"}


def test_una_etiqueta_existente_no_se_duplica(client, db, crear_item):
    uno = crear_item(title="Uno")
    dos = crear_item(title="Dos")
    guardar(client, uno, tags="documental")
    guardar(client, dos, tags="documental")
    assert db.query(Tag).filter(Tag.name == "documental").count() == 1


def test_una_etiqueta_sin_usar_se_borra(client, db, crear_item):
    """La tabla crecía monótonamente: nada borraba una etiqueta al dejar de
    usarse, así que un autocompletado ofrecería etiquetas que ya no usa nadie."""
    item = crear_item(title="Temporal")
    guardar(client, item, tags="documental")
    assert db.query(Tag).count() == 1

    guardar(client, item, tags="")

    assert db.query(Tag).count() == 0


def test_solo_se_borran_las_que_no_usa_nadie(client, db, crear_item):
    uno = crear_item(title="Uno")
    dos = crear_item(title="Dos")
    guardar(client, uno, tags="compartida, propia-de-uno")
    guardar(client, dos, tags="compartida")

    guardar(client, uno, tags="compartida")

    nombres = {t.name for t in db.query(Tag).all()}
    assert nombres == {"compartida"}


def test_borrar_un_item_no_deja_su_etiqueta_huerfana(client, db, crear_item):
    item = crear_item(title="Para borrar")
    guardar(client, item, tags="efimera")
    assert db.query(Tag).count() == 1

    client.post("/item/%d/eliminar" % item.id, follow_redirects=False)
    # La limpieza corre al guardar, no al borrar: la etiqueta sobrevive hasta
    # el siguiente guardado. Lo que no puede pasar es que quede colgada de un
    # ítem inexistente (MC-M6 ya limpia la tabla puente).
    otro = crear_item(title="Otro")
    guardar(client, otro, tags="nueva")

    assert {t.name for t in db.query(Tag).all()} == {"nueva"}
