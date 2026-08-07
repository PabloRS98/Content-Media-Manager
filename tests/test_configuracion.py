"""Tests de `app.config`: de dónde se lee la configuración y qué se rechaza.

El fallo que motiva este fichero es silencioso por naturaleza: la app arranca
igual, sin error, solo que sin autenticación y sin claves de API. Por eso los
tests miran la ruta configurada y no el resultado de un arranque correcto.
"""
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_el_aviso_dice_si_el_env_existe(caplog, monkeypatch, tmp_path):
    """Nombrar la ruta a secas manda a mirar donde no hay nada: dentro del
    contenedor `/app/.env` no existe --la configuración llega por el env_file
    del compose-- y el aviso parecía señalar un fichero perdido."""
    from app import main

    monkeypatch.setattr(main, "ENV_FILE", tmp_path / "no-existe" / ".env")
    with caplog.at_level("WARNING"):
        main.avisar_si_no_hay_autenticacion(enable_auth=False)
    assert "NO existe" in caplog.text
    assert "env_file" in caplog.text

    caplog.clear()
    presente = tmp_path / ".env"
    presente.write_text("", encoding="utf-8")
    monkeypatch.setattr(main, "ENV_FILE", presente)
    with caplog.at_level("WARNING"):
        main.avisar_si_no_hay_autenticacion(enable_auth=False)
    assert "NO existe" not in caplog.text
    assert "existe" in caplog.text


def test_no_avisa_cuando_la_autenticacion_esta_activada(caplog):
    from app.main import avisar_si_no_hay_autenticacion

    with caplog.at_level("WARNING"):
        avisar_si_no_hay_autenticacion(enable_auth=True)
    assert caplog.text == ""


class TestValidadorDeContrasena:
    """Con la autenticación activada, la app no debe arrancar insegura.

    Los tres tests pasan `_env_file=None` para no leer el `.env` real: lo que se
    prueba es el validador, no la configuración de esta máquina.
    """

    def test_no_arranca_con_la_contrasena_de_fabrica(self):
        with pytest.raises(ValidationError):
            config.Settings(
                _env_file=None, enable_auth=True, auth_password=config.DEFAULT_PASSWORD
            )

    def test_no_arranca_con_contrasena_corta(self):
        corta = "a" * (config.MIN_PASSWORD_LENGTH - 1)
        with pytest.raises(ValidationError):
            config.Settings(_env_file=None, enable_auth=True, auth_password=corta)

    def test_arranca_con_contrasena_valida(self):
        ajustes = config.Settings(
            _env_file=None, enable_auth=True, auth_password="una-contrasena-larga"
        )
        assert ajustes.enable_auth is True

    def test_sin_autenticacion_la_contrasena_de_fabrica_no_estorba(self):
        """Sin `ENABLE_AUTH` la contraseña no se usa para nada, y exigirla
        rompería el arranque por defecto que documenta el README."""
        ajustes = config.Settings(
            _env_file=None, enable_auth=False, auth_password=config.DEFAULT_PASSWORD
        )
        assert ajustes.enable_auth is False


class TestBindingDelPuerto:
    def test_el_compose_publica_en_loopback_por_defecto(self):
        """`0.0.0.0` publicaba el catálogo entero a toda la LAN sin que nada lo
        dijera. El valor por defecto es ahora loopback y se abre a propósito
        con MEDIA_BIND."""
        compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
        assert "${MEDIA_BIND:-127.0.0.1}:${MEDIA_PORT:-8002}:8000" in compose
        assert "0.0.0.0:${MEDIA_PORT" not in compose

    def test_el_env_example_documenta_el_binding_y_la_contrasena(self):
        ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
        assert "MEDIA_BIND" in ejemplo
        # El ejemplo tiene que avisar de que la app se niega a arrancar con la
        # contraseña de fábrica; si no, el fallo aparece por sorpresa.
        assert "changeme" in ejemplo and "refuses to start" in ejemplo.lower()
