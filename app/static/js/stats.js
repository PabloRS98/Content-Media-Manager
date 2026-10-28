// [R3-B6] Gráficas de /estadisticas. Antes eran un <script> en línea de
// stats.html con los números incrustados; ahora los datos llegan en el bloque
// <script type="application/json" id="stats-data"> (que el navegador no
// ejecuta, así que la CSP no necesita 'unsafe-inline' en script-src) y el
// código es un archivo estático. Cada gráfica tiene además su tabla de datos
// en la página ([R3-B28]).
document.addEventListener("DOMContentLoaded", () => {
    const bloque = document.getElementById("stats-data");
    if (!bloque || typeof Chart === "undefined") return;
    const datos = JSON.parse(bloque.textContent);

    // Lee los tokens de tema en vivo en vez de hexadecimales fijos: Chart.js
    // pinta en <canvas> y no puede seguir var(--...) por su cuenta. Al cambiar
    // de tema (evento "themechange" de app.js) se vuelven a pintar todas.
    const charts = [];
    const lienzo = (id) => document.getElementById(id);

    function draw() {
        charts.splice(0).forEach((c) => c.destroy());
        const tokens = getComputedStyle(document.documentElement);
        const tok = (name) => tokens.getPropertyValue(name).trim();
        // Hex → rgba: el canvas no entiende color-mix() en todos los navegadores.
        const alpha = (hex, pct) => {
            const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
            if (!m) return hex;
            return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${pct / 100})`;
        };

        Chart.defaults.color = tok("--text-2");
        Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
        Chart.defaults.borderColor = alpha(tok("--border-2"), 45);
        Chart.defaults.maintainAspectRatio = false;
        Chart.defaults.plugins.legend.labels.boxWidth = 10;
        Chart.defaults.plugins.legend.labels.usePointStyle = true;
        Chart.defaults.plugins.tooltip.backgroundColor = tok("--card-2");
        Chart.defaults.plugins.tooltip.titleColor = tok("--text");
        Chart.defaults.plugins.tooltip.bodyColor = tok("--text-2");
        Chart.defaults.plugins.tooltip.borderColor = tok("--border-2");
        Chart.defaults.plugins.tooltip.borderWidth = 1;
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 8;
        const PALETTE = [
            tok("--accent"), tok("--stamp"), tok("--c-serie"), tok("--c-videojuego"),
            tok("--c-podcast"), tok("--warning"), tok("--accent-soft"), tok("--text-3"),
        ];
        const gridless = { grid: { display: false } };
        const ints = { ticks: { precision: 0 }, grid: { color: alpha(tok("--border-2"), 35) }, border: { display: false } };

        charts.push(new Chart(lienzo("chart-tipos"), {
            type: "doughnut",
            data: {
                labels: ["Libros", "Películas", "Series", "Videojuegos", "Podcasts"],
                datasets: [{
                    data: datos.tipos,
                    backgroundColor: [tok("--c-libro"), tok("--c-pelicula"), tok("--c-serie"), tok("--c-videojuego"), tok("--c-podcast")],
                    borderColor: tok("--card"), borderWidth: 3, hoverOffset: 6,
                }]
            },
            options: { cutout: "66%", plugins: { legend: { position: "right" } } }
        }));

        charts.push(new Chart(lienzo("chart-meses"), {
            type: "bar",
            data: {
                labels: ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"],
                datasets: [{ label: "Completados", data: datos.meses, backgroundColor: tok("--accent"), borderRadius: 6, maxBarThickness: 28 }]
            },
            options: { plugins: { legend: { display: false } }, scales: { x: gridless, y: ints } }
        }));

        if (lienzo("chart-generos")) {
            charts.push(new Chart(lienzo("chart-generos"), {
                type: "bar",
                data: {
                    labels: datos.generos,
                    datasets: [{ data: datos.generos_n, backgroundColor: PALETTE, borderRadius: 6, maxBarThickness: 22 }]
                },
                options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: ints, y: gridless } }
            }));
        }

        if (lienzo("chart-decada")) {
            charts.push(new Chart(lienzo("chart-decada"), {
                type: "bar",
                data: {
                    labels: datos.decada.map((d) => d + "s"),
                    datasets: [{ data: datos.decada_n, backgroundColor: tok("--c-serie"), borderRadius: 6, maxBarThickness: 28 }]
                },
                options: { plugins: { legend: { display: false } }, scales: { x: gridless, y: ints } }
            }));
        }

        // La nota se guarda 1-10 y se enseña en estrellas: cada punto, media.
        charts.push(new Chart(lienzo("chart-notas"), {
            type: "bar",
            data: {
                labels: ["½", "1", "1½", "2", "2½", "3", "3½", "4", "4½", "5"].map((s) => s + "★"),
                datasets: [{ label: "Ítems", data: datos.notas, backgroundColor: tok("--star"), borderRadius: 6, maxBarThickness: 28 }]
            },
            options: { plugins: { legend: { display: false } }, scales: { x: gridless, y: ints } }
        }));
    }

    draw();
    document.addEventListener("themechange", draw);
});
