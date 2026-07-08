// ============================================================
// Home Apps Suite - JS compartido (idéntico en las 3 apps)
// Toasts (flash cookie + HTMX), spinners en botones,
// confirmaciones y diálogos <dialog>. Sin dependencias.
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
        el.innerHTML =
            '<svg class="icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICONS[category] + "</svg>" +
            "<span></span>";
        el.querySelector("span").textContent = message;
        const close = () => {
            el.classList.add("leaving");
            setTimeout(() => el.remove(), 200);
        };
        el.addEventListener("click", close);
        setTimeout(close, 4000);
        box.appendChild(el);
    }

    function setFlashCookie(message, category) {
        const value = encodeURIComponent(JSON.stringify({ m: message, c: category || "success" }));
        document.cookie = "flash=" + value + "; path=/; max-age=8; samesite=lax";
    }

    // Expuesto para otros scripts (ej. voice.js)
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

    // ---------- Diálogos <dialog> ----------
    document.addEventListener("click", (event) => {
        const opener = event.target.closest("[data-open-dialog]");
        if (opener) {
            event.preventDefault();
            const dlg = document.querySelector(opener.dataset.openDialog);
            if (dlg) dlg.showModal();
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

    // ---------- Integración HTMX (si está cargado) ----------
    // El backend puede responder con el header HX-Trigger: {"showToast": {"message": "...", "category": "..."}}
    document.body.addEventListener("showToast", (event) => {
        const d = event.detail || {};
        showToast(d.message || d.value, d.category);
    });

    consumeFlash();
})();
