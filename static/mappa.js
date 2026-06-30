/* Mappa concettuale: ogni bega (nodo colorato) punta al suo fornitore.
   Layout deterministico a cluster radiali, niente librerie esterne.

   Nota: le dimensioni dei rettangoli si calcolano da getBBox() del testo,
   ma getBBox() e' affidabile solo dopo che il browser ha fatto il layout.
   Per questo i nodi vengono creati subito e poi misurati/dimensionati
   dentro un requestAnimationFrame (vedi finalize()). */
(function () {
    "use strict";

    const SVGNS = "http://www.w3.org/2000/svg";
    const svg = document.getElementById("map-svg");
    const viewport = document.getElementById("map-viewport");
    const canvas = document.getElementById("map-canvas");
    const tip = document.getElementById("map-tip");

    function el(name, attrs) {
        const e = document.createElementNS(SVGNS, name);
        if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }

    function dataIt(iso) {
        if (!iso) return "—";
        return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
    }

    function tronca(s, n) {
        return s.length > n ? s.slice(0, n - 1) + "…" : s;
    }

    // ----- Layout: un cluster per fornitore, beghe in cerchio attorno ----- //
    function layout(grafo) {
        const cols = Math.max(1, Math.ceil(Math.sqrt(grafo.length)));
        const CELL = 400;
        grafo.forEach((f, i) => {
            f.x = (i % cols) * CELL + CELL / 2;
            f.y = Math.floor(i / cols) * CELL + CELL / 2;
            const n = f.beghe.length;
            const R = Math.max(95, (n * 34) / (2 * Math.PI) + 70);
            f.beghe.forEach((b, j) => {
                const ang = (2 * Math.PI * j) / Math.max(n, 1) - Math.PI / 2;
                b.x = f.x + R * Math.cos(ang);
                b.y = f.y + R * Math.sin(ang);
            });
        });
    }

    // Punto sul bordo del rettangolo (centrato in cx,cy) in direzione di from
    function bordoRett(cx, cy, hw, hh, fromx, fromy) {
        const ux = fromx - cx, uy = fromy - cy;
        const s = 1 / Math.max(Math.abs(ux) / hw, Math.abs(uy) / hh || 1e-6);
        return [cx + ux * s, cy + uy * s];
    }

    // Crea un nodo (rect + label) senza dimensionarlo ancora.
    function creaNodo(group, label) {
        const rect = el("rect");
        const text = el("text", { "text-anchor": "middle", dy: "0.32em", class: "node-label" });
        text.textContent = label;
        group.appendChild(rect);
        group.appendChild(text);
        return { rect, text };
    }

    // Dimensiona il rect attorno al testo (da chiamare a layout avvenuto).
    function dimensiona(rect, text, kind) {
        const bb = text.getBBox();
        const padX = kind === "fornitore" ? 18 : 11;
        const padY = kind === "fornitore" ? 11 : 7;
        const w = bb.width + padX * 2;
        const h = bb.height + padY * 2;
        rect.setAttribute("x", -w / 2);
        rect.setAttribute("y", -h / 2);
        rect.setAttribute("width", w);
        rect.setAttribute("height", h);
        rect.setAttribute("rx", kind === "fornitore" ? 9 : 13);
        return { w, h };
    }

    // ----- Tooltip ----- //
    function mostraTip(html, e) { tip.innerHTML = html; tip.hidden = false; muoviTip(e); }
    function muoviTip(e) {
        const r = canvas.getBoundingClientRect();
        let x = e.clientX - r.left + 14;
        let y = e.clientY - r.top + 14;
        if (x + tip.offsetWidth > r.width) x = e.clientX - r.left - tip.offsetWidth - 14;
        if (y + tip.offsetHeight > r.height) y = e.clientY - r.top - tip.offsetHeight - 14;
        tip.style.left = x + "px";
        tip.style.top = y + "px";
    }
    function nascondiTip() { tip.hidden = true; }

    function agganciaHandlers(g, html, url) {
        g.addEventListener("mouseenter", (e) => mostraTip(html, e));
        g.addEventListener("mousemove", muoviTip);
        g.addEventListener("mouseleave", nascondiTip);
        g.addEventListener("click", () => { if (!dragged) location.href = url; });
    }

    // ----- Costruzione grafo ----- //
    let nodesGroup = null;

    function build(grafo) {
        layout(grafo);
        viewport.textContent = "";
        const links = el("g");
        const nodes = el("g");
        viewport.appendChild(links);
        viewport.appendChild(nodes);
        nodesGroup = nodes;

        const fItems = [];
        const bItems = [];

        // nodi fornitore
        grafo.forEach((f) => {
            const g = el("g", { transform: `translate(${f.x},${f.y})`, class: "node fornitore" });
            const { rect, text } = creaNodo(g, tronca(f.nome, 26));
            agganciaHandlers(g, `<b>${f.nome}</b><br>${f.beghe.length} beghe`, f.url);
            nodes.appendChild(g);
            fItems.push({ f, rect, text });
        });

        // nodi bega
        grafo.forEach((f) => {
            f.beghe.forEach((b) => {
                const g = el("g", { transform: `translate(${b.x},${b.y})`, class: "node bega " + b.colore });
                const { rect, text } = creaNodo(g, tronca(b.titolo, 22));
                const html =
                    `<b>${b.titolo}</b><br>Stato: ${b.stato} · Priorità: ${b.priorita}` +
                    `<br>Categoria: ${b.categoria || "—"}<br>Consegna: ${dataIt(b.consegna)}`;
                agganciaHandlers(g, html, f.url);
                nodes.appendChild(g);
                bItems.push({ b, f, rect, text });
            });
        });

        // misura e disegna (a layout avvenuto)
        function finalize() {
            fItems.forEach(({ f, rect, text }) => {
                const d = dimensiona(rect, text, "fornitore");
                f.hw = d.w / 2;
                f.hh = d.h / 2;
            });
            bItems.forEach(({ rect, text }) => dimensiona(rect, text, "bega"));

            grafo.forEach((f) => {
                f.beghe.forEach((b) => {
                    const [ex, ey] = bordoRett(f.x, f.y, f.hw + 4, f.hh + 4, b.x, b.y);
                    links.appendChild(el("line", {
                        x1: b.x, y1: b.y, x2: ex, y2: ey,
                        class: "link link-" + b.colore, "marker-end": "url(#arrow)",
                    }));
                });
            });

            fit();
        }
        requestAnimationFrame(finalize);
    }

    // ----- Pan & zoom ----- //
    const view = { x: 0, y: 0, k: 1 };
    function apply() {
        viewport.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
    }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function zoomAt(factor, mx, my) {
        const wx = (mx - view.x) / view.k;
        const wy = (my - view.y) / view.k;
        view.k = clamp(view.k * factor, 0.2, 3);
        view.x = mx - wx * view.k;
        view.y = my - wy * view.k;
        apply();
    }

    let dragged = false;
    let panning = false;
    let startX = 0, startY = 0, originX = 0, originY = 0;

    svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const r = svg.getBoundingClientRect();
        zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    svg.addEventListener("mousedown", (e) => {
        panning = true; dragged = false;
        startX = e.clientX; startY = e.clientY;
        originX = view.x; originY = view.y;
    });
    window.addEventListener("mousemove", (e) => {
        if (!panning) return;
        const dx = e.clientX - startX, dy = e.clientY - startY;
        if (Math.abs(dx) + Math.abs(dy) > 4) dragged = true;
        view.x = originX + dx;
        view.y = originY + dy;
        apply();
    });
    window.addEventListener("mouseup", () => { panning = false; });

    function fit() {
        if (!nodesGroup) return;
        const bb = nodesGroup.getBBox();
        const cw = canvas.clientWidth, ch = canvas.clientHeight;
        if (!bb.width || !bb.height) return;
        const k = clamp(Math.min(cw / (bb.width + 100), ch / (bb.height + 100)), 0.2, 1.3);
        view.k = k;
        view.x = (cw - bb.width * k) / 2 - bb.x * k;
        view.y = (ch - bb.height * k) / 2 - bb.y * k;
        apply();
    }

    // ----- Avvio ----- //
    function start() {
        if (!GRAFO.length) {
            document.getElementById("map-empty").hidden = false;
            return;
        }
        build(GRAFO);
    }

    window.MAP = {
        zoom: (f) => zoomAt(f, canvas.clientWidth / 2, canvas.clientHeight / 2),
        fit: fit,
    };

    start();
})();
