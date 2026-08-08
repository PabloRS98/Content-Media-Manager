"""Configuracion centralizada via variables de entorno (.env)."""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PASSWORD = "changeme"
MIN_PASSWORD_LENGTH = 8

# Absoluta, por el mismo motivo que las plantillas y los estaticos (ver
# templating.py): con una ruta relativa el .env solo se lee si el proceso
# arranca desde la raiz del repo, y si no, pydantic-settings no encuentra el
# fichero, no lanza ningun error y la app se levanta en silencio con toda la
# configuracion por defecto -- es decir, con ENABLE_AUTH a false y sin claves.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str = "Catalogo de Medios"

    # Autenticacion HTTP Basic opcional (recomendado activar si se expone via VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = DEFAULT_PASSWORD

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

    # El scheduler (avisos + backup diario) arranca una vez por proceso. Con un
    # único worker de uvicorn (el CMD de este Dockerfile) no hay problema, pero
    # si alguna vez se añade "--workers N" habría N jobs de backup pisándose el
    # mismo fichero y N avisos de Telegram duplicados. Poner esto a false en
    # todos los workers salvo uno si se escala.
    enable_scheduler: bool = True

    # Avisos por Telegram (opcional): crea un bot con @BotFather. Vacio = sin avisos.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Clave con la que se firma la cookie de sesion (que cuenta esta abierta).
    # Vacia = se genera una sola vez y se guarda en la base, para no obligar a
    # configurar nada: el README promete que todo es opcional. Ponerla aqui
    # solo hace falta si algun dia hay varios procesos sirviendo la misma base.
    secret_key: str = ""

    # Hosts extra admitidos como origen de las peticiones que escriben (ver
    # csrf.py). Solo hace falta si se accede por un nombre distinto al del
    # proxy inverso; separados por comas.
    trusted_origins: str = ""

    def trusted_origin_hosts(self) -> set[str]:
        return {h.strip() for h in self.trusted_origins.split(",") if h.strip()}

    @model_validator(mode="after")
    def _reject_insecure_password(self) -> "Settings":
        """Con la autenticacion activada, no arrancar con la contrasena de fabrica.

        Un fallo al arrancar es ruidoso y se corrige en un minuto; una app
        expuesta con admin/changeme puede pasar meses sin que nadie lo note.
        """
        if not self.enable_auth:
            return self
        if self.auth_password == DEFAULT_PASSWORD:
            raise ValueError(
                "ENABLE_AUTH está activado pero AUTH_PASSWORD sigue siendo el valor "
                "de fábrica. Cámbialo en el .env antes de exponer la aplicación."
            )
        if len(self.auth_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                "AUTH_PASSWORD debe tener al menos %d caracteres." % MIN_PASSWORD_LENGTH
            )
        return self


settings = Settings()
