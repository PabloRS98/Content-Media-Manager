// ============================================================
// Media Catalog - JS compartido. Sin dependencias.
// Toasts (flash cookie + HTMX), spinners en botones, confirmaciones,
// diálogos <dialog>, tema claro/oscuro, atajos de teclado, carruseles,
// zonas para soltar archivos y formularios que se envían solos.
// Todo es mejora progresiva: sin este archivo la app sigue funcionando.
// ============================================================
(function () {
    "use strict";

    // ---------- Toasts ----------
    const ICONS = {
        success: '<path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/>',
        error: '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/><line x1="12" x2="12.01" y1="16" y2="16"/>',
        info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    };

    function showToast(message, category) {
        category = ICONS[category] ? category : "info";
        const box = document.getElementById("toasts");
        if (!box || !message) return;
        const el = document.createElement("div");
        el.className = "toast " + category;
        // Sin role="status"/"alert": #toasts ya es la región viva (aria-live), y
        // con role propio cada aviso se anunciaba dos veces. [R3-B31]
        el.tabIndex = 0;
        el.innerHTML =
            '<svg class="icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[category] + "</svg>" +
            "<span></span>";
        el.querySelector("span").textContent = message;
        const duracion = category === "error" ? 6000 : 4000;
        let timer = null;
        const close = () => {
            clearTimeout(timer);
            el.classList.add("leaving");
            setTimeout(() => el.remove(), 200);
        };
        // Se cierra solo, pero no mientras se lee: el ratón encima o el foco
        // dentro lo detienen, y al soltarlos la cuenta vuelve a empezar. [R3-B31]
        const pausa = () => clearTimeout(timer);
        const reanuda = () => { clearTimeout(timer); timer = setTimeout(close, duracion); };
        el.addEventListener("mouseenter", pausa);
        el.addEventListener("mouseleave", reanuda);
        el.addEventListener("focusin", pausa);
        el.addEventListener("focusout", reanuda);
        el.addEventListener("click", close);
        el.addEventListener("keydown", (e) => {
            if (e.key === "Escape" || e.key === "Enter") close();
        });
        reanuda();
        box.appendChild(el);
    }

    function setFlashCookie(message, category) {
        const value = encodeURIComponent(JSON.stringify({ m: message, c: category || "success" }));
        document.cookie = "flash=" + value + "; path=/; max-age=8; samesite=lax";
    }

    window.appToast = showToast;
    window.appFlash = setFlashCookie;

    // Al cargar: si el backend dejó una cookie "flash", mostrarla y limpiarla
    function consumeFlash() {
        const match = document.cookie.match(/(?:^|;\s*)flash=([^;]+)/);
        if (!match) return;
        document.cookie = "flash=; path=/; max-age=0";
        try {
            const data = JSON.parse(decodeURIComponent(match[1]));
            showToast(data.m, data.c);
        } catch (e) { /* cookie corrupta: se ignora */ }
    }

    // ---------- localStorage a prueba de modo privado ----------
    const store = {
        get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
        set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* sin almacenamiento */ } },
    };
    window.appStore = store;

    // ---------- Tema claro/oscuro ----------
    // El oscuro es el de por defecto (ver el script bloqueante de base.html);
    // aquí solo se cambia al pulsar y se guarda la elección. La barra del
    // navegador del móvil (theme-color) acompaña al tema.
    function syncThemeColor() {
        const meta = document.querySelector('meta[name="theme-color"]');
        if (!meta) return;
        const bg = getComputedStyle(document.body).backgroundColor;
        if (bg) meta.setAttribute("content", bg);
    }
    function toggleTheme() {
        const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", next);
        store.set("theme", next);
        syncThemeColor();
        document.dispatchEvent(new CustomEvent("themechange", { detail: next }));
    }
    document.addEventListener("click", (event) => {
        if (event.target.closest(".theme-toggle")) toggleTheme();
    });

    // ---------- Confirmaciones + spinner en formularios ----------
    document.addEventListener("submit", (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.hasAttribute("data-noload")) return;

        const confirmMsg = form.dataset.confirm || (event.submitter && event.submitter.dataset.confirm);
        if (confirmMsg && !window.confirm(confirmMsg)) {
            event.preventDefault();
            return;
        }

        const btn = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (btn && !form.hasAttribute("hx-post") && !form.hasAttribute("hx-get")) {
            // Se activa tras dejar salir el submit (si se deshabilita antes, el navegador no envía el form)
            setTimeout(() => {
                btn.classList.add("is-loading");
                btn.disabled = true;
            }, 0);
        }
    });

    // Formularios que se envían al cambiar un campo (las estrellas de la ficha).
    document.addEventListener("change", (event) => {
        const form = event.target.closest && event.target.closest("form[data-autosubmit]");
        if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
    });

    // ---------- Diálogos <dialog> ----------
    function openDialog(selector) {
        const dlg = document.querySelector(selector);
        if (dlg && !dlg.open) dlg.showModal();
        return dlg;
    }
    document.addEventListener("click", (event) => {
        const opener = event.target.closest("[data-open-dialog]");
        if (opener) {
            event.preventDefault();
            openDialog(opener.dataset.openDialog);
            return;
        }
        const closer = event.target.closest("[data-close-dialog]");
        if (closer) {
            event.preventDefault();
            const dlg = closer.closest("dialog");
            if (dlg) dlg.close();
            return;
        }
        // Clic en el fondo (fuera del contenido) cierra el diálogo
        if (event.target instanceof HTMLDialogElement) {
            const rect = event.target.getBoundingClientRect();
            const inside =
                event.clientX >= rect.left && event.clientX <= rect.right &&
                event.clientY >= rect.top && event.clientY <= rect.bottom;
            if (!inside) event.target.close();
        }
    });

    // ---------- Atajos de teclado ----------
    // "/" busca, "g" + letra navega, "?" enseña la chuleta. Nunca mientras se
    // escribe en un campo, ni con modificadores (no pisar los del navegador).
    const GOTO = {
        i: "/", c: "/catalogo", l: "/catalogo?tipo=libro", p: "/catalogo?tipo=pelicula",
        s: "/catalogo?tipo=serie", j: "/catalogo?tipo=videojuego", o: "/catalogo?tipo=podcast",
        y: "/listas", d: "/actividad", e: "/estadisticas", k: "/calendario",
    };
    let pendingG = 0;

    function isTyping(el) {
        return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
    }
    function focusSearch() {
        // El buscador de la página (catálogo) gana al de la barra lateral.
        const candidates = document.querySelectorAll("[data-hotkey-search]");
        for (let i = candidates.length - 1; i >= 0; i--) {
            const el = candidates[i];
            if (el.offsetParent !== null) { el.focus(); el.select && el.select(); return true; }
        }
        window.location.href = "/catalogo";
        return true;
    }

    document.addEventListener("keydown", (event) => {
        if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
        if (isTyping(document.activeElement)) {
            if (event.key === "Escape") document.activeElement.blur();
            return;
        }
        if (document.querySelector("dialog[open]")) return;
        const key = event.key;

        if (pendingG && Date.now() - pendingG < 1200) {
            pendingG = 0;
            const dest = GOTO[key.toLowerCase()];
            if (dest) { event.preventDefault(); window.location.href = dest; }
            return;
        }
        if (key === "/") { event.preventDefault(); focusSearch(); }
        else if (key === "g") { pendingG = Date.now(); }
        else if (key === "?") { event.preventDefault(); openDialog("#atajos"); }
        else if (key === "n") {
            const add = document.querySelector("[data-hotkey-add]");
            if (add) {
                event.preventDefault();
                const panel = add.closest("details");
                if (panel) panel.open = true;
                add.focus();
            }
        } else if (key === "e") {
            const edit = document.querySelector("[data-hotkey-edit]");
            if (edit) { event.preventDefault(); edit.click(); }
        } else if (key === "t") { toggleTheme(); }
    });

    // ---------- Carruseles (rieles horizontales) ----------
    // Flechas para escritorio; en táctil se desliza con el dedo y no se ven.
    function setupRails(root) {
        root.querySelectorAll(".rail-wrap").forEach((wrap) => {
            if (wrap.dataset.ready) return;
            wrap.dataset.ready = "1";
            const rail = wrap.querySelector(".rail");
            const prev = wrap.querySelector(".rail-nav.prev");
            const next = wrap.querySelector(".rail-nav.next");
            if (!rail || !prev || !next) return;
            const update = () => {
                prev.classList.toggle("visible", rail.scrollLeft > 8);
                next.classList.toggle("visible", rail.scrollLeft + rail.clientWidth < rail.scrollWidth - 8);
            };
            const step = (dir) => rail.scrollBy({ left: dir * rail.clientWidth * 0.85, behavior: "smooth" });
            prev.addEventListener("click", () => step(-1));
            next.addEventListener("click", () => step(1));
            rail.addEventListener("scroll", update, { passive: true });
            window.addEventListener("resize", update, { passive: true });
            update();
        });
    }

    // ---------- Zonas para soltar archivos (importar) ----------
    function setupDropzones(root) {
        root.querySelectorAll(".dropzone").forEach((zone) => {
            if (zone.dataset.ready) return;
            zone.dataset.ready = "1";
            const input = zone.querySelector('input[type="file"]');
            const label = zone.querySelector(".dropzone-text");
            if (!input) return;
            const show = () => {
                const f = input.files && input.files[0];
                zone.classList.toggle("has-file", !!f);
                if (label) {
                    label.innerHTML = "";
                    if (f) {
                        const name = document.createElement("span");
                        name.className = "file-name";
                        name.textContent = f.name;
                        const size = document.createElement("span");
                        size.textContent = " · " + Math.max(1, Math.round(f.size / 1024)) + " KB";
                        label.append(name, size);
                    } else {
                        label.textContent = zone.dataset.placeholder || "";
                    }
                }
            };
            if (label) zone.dataset.placeholder = label.textContent;
            input.addEventListener("change", show);
            ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => {
                e.preventDefault();
                zone.classList.add("dragover");
            }));
            ["dragleave", "dragend", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("dragover")));
            zone.addEventListener("drop", (e) => {
                e.preventDefault();
                if (e.dataTransfer && e.dataTransfer.files.length) {
                    input.files = e.dataTransfer.files;
                    show();
                }
            });
        });
    }

    // Retraso escalonado para la animación de entrada de las tarjetas.
    function staggerCards(root) {
        root.querySelectorAll(".grid-cards").forEach((grid) => {
            Array.prototype.forEach.call(grid.children, (el, i) => el.style.setProperty("--i", Math.min(i, 12)));
        });
    }

    function enhance(root) {
        setupRails(root);
        setupDropzones(root);
        staggerCards(root);
    }

    // ---------- Integración HTMX (si está cargado) ----------
    // El backend puede responder con el header HX-Trigger: {"showToast": {"message": "...", "category": "..."}}
    document.body.addEventListener("showToast", (event) => {
        const d = event.detail || {};
        showToast(d.message || d.value, d.category);
    });
    document.body.addEventListener("htmx:afterSwap", (event) => enhance(event.target));

    enhance(document);
    syncThemeColor();
    consumeFlash();
})();
