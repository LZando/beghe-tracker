/* Home: mappa concettuale orizzontale fornitore -> commessa -> bega.
   Nodi HTML (per testo ricco), connettori SVG curvi, espandi/comprimi con +/-.
   Niente librerie esterne. */
(function () {
    "use strict";

    const mindEl = document.getElementById("mind");
    if (!mindEl) return;
    const nodesEl = document.getElementById("mind-nodes");
    const linksEl = document.getElementById("mind-links");

    const COL_X = [10, 250, 440];   // x sinistra dei tre livelli
    const COL_W = [200, 130, 320];  // larghezza nodo per livello
    const GAP = 14;                 // spazio verticale tra fratelli
    const GAP_FORN = 26;            // spazio extra tra fornitori

    // stato espansione: aperto di default (true se non esplicitamente false)
    const aperti = {};
    const isOpen = (k) => aperti[k] !== false;

    function esc(s) {
        return (s || "").replace(/[&<>"]/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    }

    // ----- costruisce l'albero dei nodi visibili dallo stato corrente ----- //
    function model() {
        const roots = [];
        ALBERI.forEach((f) => {
            const fk = "f" + f.id;
            const fn = { key: fk, level: 0, kind: "forn", label: f.nome, url: f.url,
                         count: f.aperte, collapsible: true, children: [] };
            if (isOpen(fk)) {
                f.commesse.forEach((c, ci) => {
                    const ck = fk + "-c" + ci;
                    const cn = { key: ck, level: 1, kind: "comm", label: c.nome,
                                 count: c.beghe.length, collapsible: c.beghe.length > 0, children: [] };
                    if (isOpen(ck)) {
                        c.beghe.forEach((b) =>
                            cn.children.push({ key: "b" + b.id, level: 2, kind: "bega",
                                               bega: b, collapsible: false, children: [] }));
                    }
                    fn.children.push(cn);
                });
            }
            roots.push(fn);
        });
        return roots;
    }

    function nodeHtml(n) {
        if (n.kind === "bega") {
            const b = n.bega;
            return (
                (b.has_pdf ? '<span class="m-pdf" title="PDF allegato">PDF</span>' : "") +
                '<div class="m-bega-title">' + esc(b.descrizione) + "</div>" +
                (b.azione ? '<div class="m-bega-desc">Azione: ' + esc(b.azione) + "</div>" : "") +
                '<div class="m-bega-meta"><span class="tag prio-' + b.priorita.toLowerCase() + '">' +
                    b.priorita + '</span><span class="tag stato-' +
                    b.stato.toLowerCase().replace(/ /g, "-") + '">' + b.stato + "</span></div>"
            );
        }
        const toggle = n.collapsible
            ? '<button class="m-toggle" type="button">' + (isOpen(n.key) ? "−" : "+") + "</button>"
            : '<span class="m-toggle ghost"></span>';
        return toggle +
            '<span class="m-label">' + esc(n.label) + "</span>" +
            '<span class="m-count">' + n.count + "</span>";
    }

    function render() {
        const roots = model();
        nodesEl.textContent = "";

        const all = [];
        (function collect(ns) { ns.forEach((n) => { all.push(n); collect(n.children); }); })(roots);

        // crea i nodi e misura l'altezza reale
        all.forEach((n) => {
            const div = document.createElement("div");
            div.className = "m-node m-" + n.kind + (n.kind === "bega" ? " " + n.bega.colore : "");
            div.style.width = COL_W[n.level] + "px";
            div.style.left = COL_X[n.level] + "px";
            div.innerHTML = nodeHtml(n);
            nodesEl.appendChild(div);
            n._el = div;
            n._h = div.offsetHeight;
        });

        // assegna y: foglie impilate per altezza, genitore centrato sui figli
        let cursor = 0;
        function place(n) {
            if (!n.children.length) {
                n._y = cursor + n._h / 2;
                cursor += n._h + GAP;
                return;
            }
            n.children.forEach(place);
            n._y = (n.children[0]._y + n.children[n.children.length - 1]._y) / 2;
        }
        roots.forEach((r) => { place(r); cursor += GAP_FORN; });

        // posiziona e calcola gli ingombri
        let maxB = 0, maxR = 0;
        all.forEach((n) => {
            const top = n._y - n._h / 2;
            n._el.style.top = top + "px";
            n._x = COL_X[n.level];
            maxB = Math.max(maxB, top + n._h);
            maxR = Math.max(maxR, COL_X[n.level] + COL_W[n.level]);
        });

        nodesEl.style.height = maxB + 20 + "px";
        nodesEl.style.width = maxR + 20 + "px";
        linksEl.setAttribute("width", maxR + 20);
        linksEl.setAttribute("height", maxB + 20);

        // connettori curvi genitore -> figlio
        let d = "";
        all.forEach((n) => {
            n.children.forEach((c) => {
                const x1 = n._x + COL_W[n.level], y1 = n._y, x2 = c._x, y2 = c._y;
                const mx = (x1 + x2) / 2;
                d += `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2} `;
            });
        });
        linksEl.innerHTML = '<path d="' + d + '"/>';

        // interazioni
        all.forEach((n) => {
            if (n.collapsible) {
                const t = n._el.querySelector(".m-toggle");
                if (t) t.addEventListener("click", (e) => {
                    e.stopPropagation();
                    aperti[n.key] = !isOpen(n.key);
                    render();
                });
            }
            if (n.kind === "forn") {
                n._el.querySelector(".m-label").addEventListener("click", () => { location.href = n.url; });
            }
            if (n.kind === "bega") {
                n._el.addEventListener("click", () => { location.href = n.bega.url; });
            }
        });
    }

    render();
})();
