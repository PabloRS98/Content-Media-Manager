"""Avisos por Telegram (opcional). Si no hay token/chat configurados, no hace nada.

El usuario crea un bot con @BotFather, mete su token en TELEGRAM_BOT_TOKEN y el
id de su chat en TELEGRAM_CHAT_ID (se lo da @userinfobot, por ejemplo)."""
import html
import logging

import httpx

from ..config import settings
from ._logging_utils import log_fallo_api

logger = logging.getLogger(__name__)


def esc(valor: str) -> str:
    """Escapa un valor para el HTML de Telegram.

    Los mensajes se mandan con parse_mode HTML y los títulos vienen de TMDB,
    iTunes, Google Books, RAWG y de los CSV importados. Un `&` sin escapar
    ("Marley & Me", "Will & Grace", "Dungeons & Dragons") hace que Telegram
    responda `400 Bad Request: can't parse entities` y el aviso no llegue.

    `quote=False` a propósito: Telegram solo decodifica &lt; &gt; &amp; y
    &quot;, no las entidades numéricas, así que escapar el apóstrofo dejaría
    un "&#x27;" literal en el mensaje.
    """
    return html.escape(valor, quote=False)


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_message(text: str) -> bool:
    """Envía un mensaje al chat configurado. Devuelve True si se envió."""
    if not is_configured():
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        # log_fallo_api y no logger.exception: el token va en la RUTA de la
        # URL, y el mensaje de la excepción de raise_for_status() incluye la
        # URL completa (ver _logging_utils.py). Un token filtrado permite
        # suplantar el bot y leer los mensajes pendientes con getUpdates.
        log_fallo_api(logger, "Fallo al enviar aviso de Telegram", exc=e)
        return False
