"""Tests de `app.config`: de dónde se lee la configuración y qué se rechaza.

El fallo que motiva este fichero es silencioso por naturaleza: la app arranca
igual, sin error, solo que sin autenticación y sin claves de API. Por eso los
tests miran la ruta configurada y no el resultado de un arranque correcto.
"""
import importlib.util
from pathlib import Path

from app import config

# La raíz del repositorio: dos niveles por encima de `app/config.py`.
RAIZ = Path(config.__file__).resolve().parent.parent


def cargar_config_aislado():
    """Ejecuta `app/config.py` otra vez, con un nombre de módulo propio.

    No se usa `importlib.reload(config)`: eso sustituiría el objeto
    `config.settings` que el resto de la app ya tiene importado por valor
    (`from .config import settings`), y a partir de ahí tocar `config.settings`
    en un test dejaría de afectar a `app.main`.
    """
    spec = importlib.util.spec_from_file_location("config_aislado", RAIZ / "app" / "config.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_env_file_es_una_ruta_absoluta():
    assert config.ENV_FILE.is_absolute()
    assert config.ENV_FILE == RAIZ / ".env"


def test_la_configuracion_se_lee_desde_otro_directorio(tmp_path, monkeypatch):
    """Reproduce el arranque desde otro directorio de trabajo.

    Con `env_file=".env"` (relativo al cwd) el módulo ejecutado desde `tmp_path`
    apuntaba a un fichero que no existe, pydantic-settings no se quejaba y la app
    se levantaba con todos los valores por defecto. Con la ruta absoluta, el
    fichero configurado es el mismo se arranque desde donde se arranque.
    """
    monkeypatch.chdir(tmp_path)
    aislado = cargar_config_aislado()
    assert aislado.ENV_FILE == RAIZ / ".env"
    assert Path(aislado.Settings.model_config["env_file"]) == RAIZ / ".env"


def test_avisa_al_arrancar_sin_autenticacion(caplog):
    """Un arranque sin auth tiene que dejar rastro en los logs del contenedor.

    Es la contrapartida del fallo silencioso: si el .env no se lee, la
    autenticación se desactiva sola, y este aviso es lo único que lo delata.
    """
    from app.main import avisar_si_no_hay_autenticacion

    with caplog.at_level("WARNING"):
        avisar_si_no_hay_autenticacion(enable_auth=False)
    assert "ENABLE_AUTH" in caplog.text


def test_no_avisa_cuando_la_autenticacion_esta_activada(caplog):
    from app.main import avisar_si_no_hay_autenticacion

    with caplog.at_level("WARNING"):
        avisar_si_no_hay_autenticacion(enable_auth=True)
    assert caplog.text == ""
