// [R3-B6] Tema inicial, antes de pintar nada. Era un <script> en línea de
// base.html; como archivo propio la CSP puede prohibir 'unsafe-inline' en
// script-src. Se carga bloqueante en <head> (sin defer ni async) a propósito:
// si esperase a app.js se vería un parpadeo del tema claro en cada carga. El
// try es por el modo privado de algunos navegadores, donde localStorage lanza.
// Oscuro por defecto si no ha elegido nunca.
(function () {
    "use strict";
    var tema = "dark";
    try { tema = localStorage.getItem("theme") || "dark"; } catch (e) { /* almacenamiento bloqueado */ }
    document.documentElement.setAttribute("data-theme", tema);
})();
