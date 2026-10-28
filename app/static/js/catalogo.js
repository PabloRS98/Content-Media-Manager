// [R3-B6] Antes un <script> en línea de catalog.html: la CSP ya no admite
// 'unsafe-inline' en script-src. Vista cuadrícula/lista, desplegables de
// filtro e idioma de búsqueda de libros.
(function () {
    var store = window.appStore;
    var grid = document.getElementById("catalog-grid");
    var buttons = document.querySelectorAll(".view-toggle [data-view]");
    function apply(view) {
        if (grid) grid.classList.toggle("as-list", view === "list");
        buttons.forEach(function (b) {
            b.classList.toggle("active", b.dataset.view === view);
            b.setAttribute("aria-pressed", b.dataset.view === view ? "true" : "false");
        });
    }
    apply(store.get("catalogView") || "grid");
    buttons.forEach(function (b) {
        b.addEventListener("click", function () {
            store.set("catalogView", b.dataset.view);
            apply(b.dataset.view);
        });
    });

    // Llegando desde "Añadir" de la portada: el panel abierto y el cursor dentro.
    var panel = document.getElementById("anadir");
    if (panel && location.hash === "#anadir") {
        panel.open = true;
        var q = document.getElementById("q-input");
        if (q) setTimeout(function () { q.focus(); }, 50);
    }

    // Desplegables de filtro (estado/género/duración/orden): al abrir uno se
    // cierran los demás, y un clic fuera o Escape cierra el que esté abierto
    // -- los <details> nativos por sí solos se quedan abiertos indefinidamente.
    var filterDetails = document.querySelectorAll(".filter-dd");
    filterDetails.forEach(function (d) {
        d.addEventListener("toggle", function () {
            if (d.open) filterDetails.forEach(function (other) { if (other !== d) other.open = false; });
        });
    });
    document.addEventListener("click", function (e) {
        filterDetails.forEach(function (d) {
            if (d.open && !d.contains(e.target)) d.open = false;
        });
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") filterDetails.forEach(function (d) { d.open = false; });
    });

    // Idioma de búsqueda de libros: recuerda la preferencia (como la vista de
    // arriba) y, si ya hay algo escrito, repite la búsqueda en el idioma nuevo.
    var idiomaToggle = document.getElementById("idioma-toggle");
    var idiomaInput = document.getElementById("idioma-input");
    var qInput = document.getElementById("q-input");
    if (idiomaToggle && idiomaInput) {
        var idiomaBtns = idiomaToggle.querySelectorAll("[data-idioma]");
        function aplicaIdioma(idioma) {
            idiomaInput.value = idioma;
            idiomaBtns.forEach(function (b) { b.classList.toggle("active", b.dataset.idioma === idioma); });
        }
        aplicaIdioma(store.get("bookSearchLang") || "es");
        idiomaBtns.forEach(function (b) {
            b.addEventListener("click", function () {
                store.set("bookSearchLang", b.dataset.idioma);
                aplicaIdioma(b.dataset.idioma);
                if (qInput && qInput.value.trim().length >= 2) {
                    htmx.ajax("GET", "/buscar?tipo=libro&idioma=" + b.dataset.idioma +
                        "&q=" + encodeURIComponent(qInput.value),
                        { target: "#search-results", swap: "innerHTML" });
                }
            });
        });
    }
})();
