/* Mappa concettuale a 3 livelli: fornitore -> commessa -> bega.
   Le beghe risolte sono gia' escluse dal backend.
   Layout ad albero orizzontale con connettori curvi (bezier).
   Niente librerie esterne.

   Nota: getBBox() del testo e' affidabile solo a layout avvenuto, quindi i
   rettangoli si dimensionano dentro requestAnimationFrame (vedi finalize()). */
(function () {
    "use strict";

    const SVGNS = "http://www.w3.org/2000/svg";
    const svg = document.getElementById("map-svg");
    const viewport = document.getElementById("map-viewport");
    const canvas = document.getElementById("map-canvas");
    const tip = document.getElementById("map-tip");

    // colonne x dei tre livelli e passi verticali
    const X_FORN = 110, X_COMM = 440, X_BEGA = 790;
    const STEP = 46;   // passo verticale tra beghe
    const GAP_COMM = 24, GAP_FORN = 44;

    function el(name, attrs) {
        const e = document.createElementNS(SVGNS, name);
        if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }
    function dataIt(iso) {
        if (!iso) return "—";
        return iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
    }
    function tronca(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }
    function media(arr) { return arr.reduce((a, b) => a + b, 0) / arr.length; }

    // ----- Layout: assegna x,y a fornitori/commesse/beghe ----- //
    function layout(grafo) {
        let y = 30;
        grafo.forEach((f) => {
            f.commesse.forEach((c) => {
                c.beghe.forEach((b) => { b.x = X_BEGA; b.y = y; y += STEP; });
                c.x = X_COMM;
                c.y = media(c.beghe.map((b) => b.y));
                y += GAP_COMM;
            });
            f.x = X_FORN;
            f.y = media(f.commesse.map((c) => c.y));
            y += GAP_FORN;
        });
    }

    // ----- Nodi ----- //
    function creaNodo(group, label) {
        const rect = el("rect");
        const text = el("text", { "text-anchor": "middle", dy: "0.32em", class: "node-label" });
        text.textContent = label;
        group.appendChild(rect);
        group.appendChild(text);
        return { rect, text };
    }
    function dimensiona(rect, text, kind) {
        const bb = text.getBBox();
        const padX = kind === "fornitore" ? 18 : 11;
        const padY = kind === "fornitore" ? 11 : 7;
        const w = bb.width + padX * 2, h = bb.height + padY * 2;
        rect.setAttribute("x", -w / 2);
        rect.setAttribute("y", -h / 2);
        rect.setAttribute("width", w);
        rect.setAttribute("height", h);
        rect.setAttribute("rx", kind === "fornitore" ? 9 : kind === "commessa" ? 7 : 13);
        return { w, h };
    }

    // ----- Tooltip ----- //
    function mostraTip(html, e) { tip.innerHTML = html; tip.hidden = false; muoviTip(e); }
    function muoviTip(e) {
        const r = canvas.getBoundingClientRect();
        let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
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
        if (url) g.addEventListener("click", () => { if (!dragged) location.href = url; });
    }

    // curva bezier orizzontale da (x1,y1) a (x2,y2)
    function curva(x1, y1, x2, y2) {
        const mx = (x1 + x2) / 2;
        return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    }

    // ----- Costruzione ----- //
    let nodesGroup = null;

    function build(grafo) {
        layout(grafo);
        viewport.textContent = "";
        const links = el("g");
        const nodes = el("g");
        viewport.appendChild(links);
        viewport.appendChild(nodes);
        nodesGroup = nodes;

        const items = []; // {ref, rect, text, kind}

        grafo.forEach((f) => {
            const g = el("g", { transform: `translate(${f.x},${f.y})`, class: "node fornitore" });
            const { rect, text } = creaNodo(g, tronca(f.nome, 24));
            const n = f.commesse.reduce((s, c) => s + c.beghe.length, 0);
            agganciaHandlers(g, `<b>${f.nome}</b><br>${n} beghe aperte`, f.url);
            nodes.appendChild(g);
            items.push({ ref: f, rect, text, kind: "fornitore" });

            f.commesse.forEach((c) => {
                const gc = el("g", { transform: `translate(${c.x},${c.y})`, class: "node commessa" });
                const r2 = creaNodo(gc, tronca(c.nome, 18));
                agganciaHandlers(gc, `<b>${c.nome}</b><br>${c.beghe.length} beghe`, null);
                nodes.appendChild(gc);
                items.push({ ref: c, rect: r2.rect, text: r2.text, kind: "commessa" });

                c.beghe.forEach((b) => {
                    const gb = el("g", { transform: `translate(${b.x},${b.y})`, class: "node bega " + b.colore });
                    const r3 = creaNodo(gb, tronca(b.descrizione || "(senza descrizione)", 24));
                    const html =
                        `<b>${b.descrizione || "(senza descrizione)"}</b><br>Stato: ${b.stato} · Priorità: ${b.priorita}` +
                        (b.azione ? `<br>Azione: ${b.azione}` : "") +
                        `<br>Consegna: ${dataIt(b.consegna)}`;
                    agganciaHandlers(gb, html, b.url);
                    nodes.appendChild(gb);
                    items.push({ ref: b, rect: r3.rect, text: r3.text, kind: "bega" });
                });
            });
        });

        function finalize() {
            items.forEach(({ ref, rect, text, kind }) => {
                const d = dimensiona(rect, text, kind);
                ref.hw = d.w / 2;
            });
            // connettori child -> parent (freccia verso il fornitore)
            grafo.forEach((f) => {
                f.commesse.forEach((c) => {
                    links.appendChild(el("path", {
                        d: curva(c.x - c.hw, c.y, f.x + f.hw + 4, f.y),
                        class: "link", "marker-end": "url(#arrow)", fill: "none",
                    }));
                    c.beghe.forEach((b) => {
                        links.appendChild(el("path", {
                            d: curva(b.x - b.hw, b.y, c.x + c.hw + 4, c.y),
                            class: "link link-" + b.colore, "marker-end": "url(#arrow)", fill: "none",
                        }));
                    });
                });
            });
            fit();
        }
        // getBBox e' affidabile solo a layout avvenuto: lo rinviamo.
        // rAF per il caso normale + setTimeout come fallback (rAF e' sospeso
        // quando la tab non e' in primo piano).
        let fatto = false;
        const once = () => { if (!fatto) { fatto = true; finalize(); } };
        requestAnimationFrame(once);
        setTimeout(once, 80);
    }

    // ----- Pan & zoom ----- //
    const view = { x: 0, y: 0, k: 1 };
    function apply() { viewport.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`); }
    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
    function zoomAt(factor, mx, my) {
        const wx = (mx - view.x) / view.k, wy = (my - view.y) / view.k;
        view.k = clamp(view.k * factor, 0.2, 3);
        view.x = mx - wx * view.k;
        view.y = my - wy * view.k;
        apply();
    }

    let dragged = false, panning = false;
    let startX = 0, startY = 0, originX = 0, originY = 0;

    svg.addEventListener("wheel", (e) => {
        e.preventDefault();
        const r = svg.getBoundingClientRect();
        zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    svg.addEventListener("mousedown", (e) => {
        panning = true; dragged = false;
        startX = e.clientX; startY = e.clientY; originX = view.x; originY = view.y;
    });
    window.addEventListener("mousemove", (e) => {
        if (!panning) return;
        const dx = e.clientX - startX, dy = e.clientY - startY;
        if (Math.abs(dx) + Math.abs(dy) > 4) dragged = true;
        view.x = originX + dx; view.y = originY + dy; apply();
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
