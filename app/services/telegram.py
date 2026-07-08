"""Avisos por Telegram (opcional). Si no hay token/chat configurados, no hace nada.

El usuario crea un bot con @BotFather, mete su token en TELEGRAM_BOT_TOKEN y el
id de su chat en TELEGRAM_CHAT_ID (se lo da @userinfobot, por ejemplo)."""
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


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
    except Exception:
        logger.exception("Fallo al enviar aviso de Telegram")
        return False
