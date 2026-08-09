"""Importar la biblioteca de Steam. [N6]

Steam no exporta un CSV, pero tiene una API pública que devuelve los juegos que
tienes y cuántos minutos has jugado a cada uno. Hacen falta dos cosas: una
clave (gratuita, `STEAM_API_KEY`) y tu SteamID de 17 dígitos (`STEAM_ID`).

Todo lo que entra queda marcado con plataforma "Steam", que es lo que hace que
"¿qué tengo pendiente en Steam?" funcione de verdad [N7].
"""
import logging

import httpx
from sqlalchemy.orm import Session

from ..models import MediaItem, MediaStatus, MediaType
from ._logging_utils import log_fallo_api

logger = logging.getLogger(__name__)

URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"

# Steam mide en minutos. Por debajo de esto se considera que solo lo abriste
# para ver qué era: el juego entra como pendiente, no como empezado.
MINUTOS_PARA_CONTAR_COMO_EMPEZADO = 10


def _pedir_biblioteca(clave: str, steam_id: str) -> dict:
    """La petición, aislada para poder sustituirla en los tests."""
    respuesta = httpx.get(
        URL,
        params={
            "key": clave,
            "steamid": steam_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
        },
        timeout=15,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def importar_biblioteca(db: Session, usuario_id: int, clave: str, steam_id: str) -> dict:
    """Trae la biblioteca a la cuenta indicada. Nunca lanza: devuelve `error`.

    Un importador que revienta deja media biblioteca dentro y ninguna
    explicación en pantalla; el resto de esta app ya trata los fallos de API
    así, y `log_fallo_api` se encarga de que la clave no acabe en el log.
    """
    if not clave or not steam_id:
        return {
            "creados": 0, "duplicados": 0,
            "error": "Faltan STEAM_API_KEY o STEAM_ID en la configuración",
        }

    try:
        datos = _pedir_biblioteca(clave, steam_id)
    except Exception as e:
        log_fallo_api(logger, "Fallo al pedir la biblioteca de Steam", exc=e)
        return {
            "creados": 0, "duplicados": 0,
            "error": "Steam no respondió. Revisa la clave y el SteamID.",
        }

    juegos = (datos.get("response") or {}).get("games") or []
    if not juegos:
        return {
            "creados": 0, "duplicados": 0,
            "error": "Steam no devolvió ningún juego. ¿Es el SteamID correcto y "
                     "el perfil público?",
        }

    existentes = {
        (i.title or "").lower()
        for i in db.query(MediaItem).filter(
            MediaItem.media_type == MediaType.VIDEOJUEGO,
            MediaItem.usuario_id == usuario_id,
        ).all()
    }

    creados = duplicados = 0
    for juego in juegos:
        titulo = (juego.get("name") or "").strip()
        if not titulo:
            continue
        if titulo.lower() in existentes:
            duplicados += 1
            continue

        minutos = juego.get("playtime_forever") or 0
        db.add(MediaItem(
            usuario_id=usuario_id,
            media_type=MediaType.VIDEOJUEGO,
            title=titulo,
            external_id=str(juego.get("appid")) if juego.get("appid") else None,
            external_source="steam",
            plataforma="Steam",
            # Las horas de Steam son horas JUGADAS, no las que dura el juego.
            # Se guardan en el mismo sitio que las de HowLongToBeat porque es
            # el campo de "cuánto tiempo lleva esto", y para lo empezado es el
            # dato más fiel que hay.
            hltb_hours=round(minutos / 60, 1) if minutos else None,
            status=(MediaStatus.EN_PROGRESO
                    if minutos >= MINUTOS_PARA_CONTAR_COMO_EMPEZADO
                    else MediaStatus.PENDIENTE),
        ))
        existentes.add(titulo.lower())
        creados += 1

    db.commit()
    return {"creados": creados, "duplicados": duplicados, "error": None}
