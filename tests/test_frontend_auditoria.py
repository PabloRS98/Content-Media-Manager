"""Frontend y accesibilidad de la auditoría 3 (R3-M26..M30, R3-B6, B7, B27..B34).

Casi todo es CSS/HTML estático, así que los tests leen los archivos o las
páginas renderizadas: no pueden demostrar que algo "se vea bien", pero sí que
no vuelva a desaparecer lo que se arregló.
"""
import colorsys
import json
import re
from pathlib import Path

import pytest

ESTATICOS = Path(__file__).resolve().parent.parent / "app" / "static"
CSS = (ESTATICOS / "css" / "style.css").read_text(encoding="utf-8")

PAGINAS = ["/", "/catalogo", "/listas", "/estadisticas", "/importar", "/estado", "/actividad", "/calendario"]


def _luminancia_rgb(r: float, g: float, b: float) -> float:
    lineales = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lineales[0] + 0.7152 * lineales[1] + 0.0722 * lineales[2]


def _luminancia(hexa: str) -> float:
    return _luminancia_rgb(*[int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5)])


def _ratio(la: float, lb: float) -> float:
    alta, baja = max(la, lb), min(la, lb)
    return (alta + 0.05) / (baja + 0.05)


def _contraste(a: str, b: str) -> float:
    return _ratio(_luminancia(a), _luminancia(b))


def _tokens(selector: str) -> dict:
    bloque = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", bloque))


def _tokens_del_tema(tema: str) -> dict:
    """Los del tema, completados con los claros (el oscuro solo redefine los
    que cambian)."""
    claros = {}
    for bloque in re.findall(r"(?m)^:root\s*\{(.*?)\n\}", CSS, re.S):
        claros.update(dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", bloque)))
    return {**claros, **(_tokens(tema) if tema != ":root" else {})}


# ---------- R3-B6: CSP sin 'unsafe-inline' en scripts ----------

def test_la_csp_no_admite_scripts_en_linea(client):
    csp = client.get("/").headers["content-security-policy"]
    directiva = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in directiva
    assert "'self'" in directiva


@pytest.mark.parametrize("ruta", PAGINAS)
def test_ninguna_pagina_lleva_scripts_ni_manejadores_en_linea(client, ruta):
    html = client.get(ruta).text
    # Un <script> sin src solo vale si es un bloque de datos (no se ejecuta).
    for etiqueta in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in etiqueta or "application/json" in etiqueta, etiqueta
    assert not re.search(r"""<[^>]+\son[a-z]+\s*=""", html), "manejador on*= en línea"
    assert "javascript:" not in html


def test_el_tema_se_fija_desde_un_script_bloqueante_en_head(client):
    cabecera = client.get("/").text.split("</head>")[0]
    etiqueta = re.search(r'<script src="/static/js/tema\.js[^"]*"[^>]*>', cabecera)
    assert etiqueta, "tema.js debe cargarse en <head>"
    assert "defer" not in etiqueta.group(0) and "async" not in etiqueta.group(0)


def test_las_graficas_reciben_los_datos_por_un_bloque_json(client, crear_item):
    from app.models import MediaStatus

    crear_item(title="Mío", genres="Fantasia", year=2020, rating=7, status=MediaStatus.COMPLETADO)
    html = client.get("/estadisticas").text
    bloque = re.search(r'<script type="application/json" id="stats-data">(.*?)</script>', html, re.S)
    datos = json.loads(bloque.group(1))
    assert len(datos["tipos"]) == 5 and len(datos["meses"]) == 12 and len(datos["notas"]) == 10
    # Las filas de SQLAlchemy (género, n) llegan convertidas a listas.
    assert datos["generos"] == ["Fantasia"] and datos["generos_n"] == [1]
    assert datos["decada"] == [2020] and datos["decada_n"] == [1]
    assert (ESTATICOS / "js" / "stats.js").exists()


# ---------- R3-B7: cabeceras ----------

def test_el_html_no_se_guarda_en_cache(client):
    for ruta in PAGINAS:
        assert client.get(ruta).headers["cache-control"] == "no-store", ruta


def test_los_estaticos_conservan_su_cache(client):
    sin_huella = client.get("/static/css/style.css")
    assert sin_huella.headers["cache-control"] == "no-cache"
    con_huella = client.get("/static/css/style.css?v=abc")
    assert "immutable" in con_huella.headers["cache-control"]


def test_la_salud_sigue_respondiendo(client):
    r = client.get("/salud")
    assert r.status_code == 200
    # Es JSON, no HTML: no le corresponde el no-store de las páginas.
    assert "no-store" not in r.headers.get("cache-control", "")


def test_hay_permissions_policy(client):
    politica = client.get("/").headers["permissions-policy"]
    for capacidad in ("camera=()", "microphone=()", "geolocation=()"):
        assert capacidad in politica


# ---------- R3-M26: manifiesto ----------

def test_el_manifiesto_tiene_iconos_192_512_y_maskable_con_paleta_oscura(client):
    manifiesto = json.loads((ESTATICOS / "manifest.webmanifest").read_text(encoding="utf-8"))
    iconos = manifiesto["icons"]
    assert any(i["sizes"] == "192x192" for i in iconos)
    assert any(i["sizes"] == "512x512" and i.get("purpose", "any") == "any" for i in iconos)
    assert any("maskable" in i.get("purpose", "") for i in iconos)
    for icono in iconos:
        assert (ESTATICOS / icono["src"].removeprefix("/static/")).exists(), icono["src"]
    oscuro = _tokens(':root[data-theme="dark"]')
    assert manifiesto["background_color"] == oscuro["bg"]
    assert manifiesto["theme_color"] == manifiesto["background_color"]
    # El <meta theme-color> inicial coincide (app.js lo sincroniza después).
    assert f'<meta name="theme-color" content="{oscuro["bg"]}">' in client.get("/").text


# ---------- R3-M27: flechas del carrusel con teclado ----------

def test_las_flechas_del_carrusel_se_ven_con_el_foco():
    assert re.search(r"\.rail-wrap:focus-within \.rail-nav\.visible", CSS)
    assert ".rail-nav:focus-visible" in CSS


# ---------- R3-M29 / B27 / B28: accesibilidad del HTML ----------

def test_los_contenedores_de_resultados_htmx_son_regiones_vivas(client):
    assert 'id="search-results" aria-live="polite"' in client.get("/catalogo").text
    home = client.get("/").text
    assert 'id="timefit-box" aria-live="polite"' in home
    assert 'id="suggestion-box" aria-live="polite"' in home
    assert 'id="enrich-result" aria-live="polite"' in client.get("/importar").text


@pytest.mark.parametrize("ruta", PAGINAS)
def test_la_jerarquia_de_titulos_no_salta_niveles(client, ruta):
    niveles = [int(n) for n in re.findall(r"<h([1-6])\b", client.get(ruta).text)]
    assert niveles.count(1) == 1, niveles
    previo = 0
    for nivel in niveles:
        assert nivel <= previo + 1, niveles
        previo = nivel


def test_cada_grafica_tiene_su_alternativa_textual(client, crear_item):
    from app.models import MediaStatus

    crear_item(title="Mío", genres="Fantasia", year=2020, rating=7, status=MediaStatus.COMPLETADO)
    html = client.get("/estadisticas").text
    for lienzo in ("chart-tipos", "chart-meses", "chart-generos", "chart-decada", "chart-notas"):
        assert re.search(rf'<canvas id="{lienzo}" role="img" aria-label="[^"]+"', html), lienzo
    assert html.count('<details class="chart-data">') == 5
    assert html.count("<caption") == 5


# ---------- R3-B30: hoja "Más" ----------

def test_el_asa_de_la_hoja_mas_es_un_boton_con_nombre():
    # Se lee la plantilla y no la página: la hoja solo se pinta con una cuenta
    # abierta, y el cliente de los tests inyecta el usuario sin abrirla.
    base = (ESTATICOS.parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert '<button type="button" class="sheet-handle" data-close-dialog aria-label="Cerrar"></button>' in base
    assert '<div class="sheet-handle"' not in base


# ---------- R3-B31: avisos ----------

def test_los_avisos_se_pausan_con_raton_y_foco_y_no_se_anuncian_dos_veces(client):
    js = (ESTATICOS / "js" / "app.js").read_text(encoding="utf-8")
    for evento in ("mouseenter", "mouseleave", "focusin", "focusout"):
        assert evento in js
    # La región viva es el contenedor; un role en cada aviso duplicaría el anuncio.
    assert 'setAttribute("role"' not in js
    html = client.get("/").text
    assert re.search(r'<div id="toasts"[^>]*aria-live="polite"', html)


# ---------- R3-B32 / B33: contraste ----------

@pytest.mark.parametrize("tema", [":root", ':root[data-theme="dark"]'])
def test_el_borde_de_los_campos_contrasta_3_a_1_con_su_entorno(tema):
    t = _tokens_del_tema(tema)
    for fondo in ("bg", "bg-soft", "card", "card-2"):
        assert _contraste(t["field-border"], t[fondo]) >= 3.0, (tema, fondo)
    assert "border: 1px solid var(--field-border)" in CSS


def test_la_inicial_del_avatar_contrasta_al_menos_4_5_con_cualquier_tono():
    """Recorre los 360 tonos posibles (`--h` sale de un módulo 360) con los
    parámetros reales del degradado leídos del CSS."""
    m = re.search(
        r"linear-gradient\(145deg, hsl\(var\(--h, 38\) (\d+)% (\d+)%\), "
        r"hsl\(calc\(var\(--h, 38\) \+ 30\) (\d+)% (\d+)%\)\)",
        CSS,
    )
    s1, l1, s2, l2 = (int(x) for x in m.groups())
    texto = _luminancia("#fbf3e2")
    peor = 99.0
    for h in range(360):
        for tono, s, lum in ((h, s1, l1), (h + 30, s2, l2)):
            rgb = colorsys.hls_to_rgb((tono % 360) / 360, lum / 100, s / 100)
            peor = min(peor, _ratio(texto, _luminancia_rgb(*rgb)))
    assert peor >= 4.5, peor


# ---------- R3-B34 / B29 / M28 / M30: CSS ----------

@pytest.mark.parametrize("clase", ["cover-progress", "card-title", "section-plain", "inline-form", "month-nav"])
def test_el_css_sin_uso_se_ha_borrado(clase):
    assert f".{clase}" not in CSS


def test_no_hay_view_transition_dentro_de_reduced_motion_reduce():
    for bloque in re.findall(r"@media[^{]*prefers-reduced-motion:\s*reduce[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", CSS):
        assert "@view-transition" not in bloque
    # Y solo se activa si el usuario admite movimiento.
    assert re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{\s*@view-transition \{ navigation: auto; \}", CSS
    )


def test_hay_objetivos_tactiles_de_44px_bajo_1024px():
    bloque = re.search(r"@media \(max-width: 1023px\) \{(.*?)\n\}", CSS, re.S).group(1)
    assert ".ep-check" in bloque and ".icon-btn" in bloque and ".star-input label" in bloque
    assert "min-height: 44px" in bloque and "min-width: 44px" in bloque
    assert "height: 44px" in bloque


def test_hay_estilos_de_impresion_que_ocultan_la_navegacion():
    bloque = re.search(r"@media print \{(.*)", CSS, re.S).group(1)
    for selector in (".sidebar", ".topbar", ".tabbar", "dialog", ".toasts", "button", "form"):
        assert selector in bloque, selector
    assert "#fff" in bloque and "#000" in bloque
