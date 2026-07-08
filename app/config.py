"""Configuracion centralizada via variables de entorno (.env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Catalogo de Medios"

    # Autenticacion HTTP Basic opcional (recomendado activar si se expone via VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Base de datos SQLite
    db_path: str = "/data/media.db"

    # Nº de backups diarios de la BD que se conservan en /data/backups
    backup_keep: int = 14

    # Claves gratuitas para autocompletar metadatos (dejar vacio desactiva esa busqueda)
    tmdb_api_key: str = ""
    rawg_api_key: str = ""
    google_books_api_key: str = ""

    # Zona horaria (para el job de estrenos)
    timezone: str = "UTC"

    # Avisos por Telegram (opcional): crea un bot con @BotFather. Vacio = sin avisos.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
