"""Avisos por Telegram (opcional). Si no hay token/chat configurados, no hace nada.

El usuario crea un bot con @BotFather, mete su token en TELEGRAM_BOT_TOKEN y el
id de su chat en TELEGRAM_CHAT_ID (se lo da @userinfobot, por ejemplo).

Es una versión reducida a propósito del cliente de finance-tracker: aquí la
app solo ENVÍA avisos, así que no hay `edit_message`, ni `answer_callback`,
ni long polling. Si algún día se quiere responder a comandos desde el chat,
ese es el sitio de donde copiarlo, no algo que falte por descuido."""
import html
import logging
import time

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


# Lo más que se espera a que Telegram deje volver a enviar tras un 429. Más
# que esto y el mensaje se queda para la siguiente pasada del job (no se marca
# como avisado), en lugar de dejar el hilo del scheduler dormido.
ESPERA_MAXIMA_429 = 30


def send_message(text: str) -> bool:
    """Envía un mensaje al chat configurado. Devuelve True si se envió.

    Ante un 429 (demasiados mensajes seguidos) espera lo que pide Telegram y
    lo reintenta una vez. [R3-B11]
    """
    if not is_configured():
        return False
    for intento in range(2):
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=10,
            )
            if resp.status_code == 429 and intento == 0:
                espera = _retry_after(resp)
                if espera is not None and espera <= ESPERA_MAXIMA_429:
                    time.sleep(espera)
                    continue
            resp.raise_for_status()
            return True
        except Exception as e:
            # log_fallo_api y no logger.exception: el token va en la RUTA de la
            # URL, y el mensaje de la excepción de raise_for_status() incluye la
            # URL completa (ver _logging_utils.py). Un token filtrado permite
            # suplantar el bot y leer los mensajes pendientes con getUpdates.
            log_fallo_api(logger, "Fallo al enviar aviso de Telegram", exc=e)
            return False
    return False


def _retry_after(resp) -> int | None:
    try:
        return int(resp.json()["parameters"]["retry_after"])
    except Exception:
        try:
            return int(resp.headers.get("retry-after", ""))
        except ValueError:
            return None
