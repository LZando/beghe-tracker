"""
Beghe Tracker — checklist delle problematiche ("beghe") per fornitore.

Stack: Flask + SQLite (standard library + Werkzeug, gia' incluso con Flask).
Avvio:  python app.py   ->   http://127.0.0.1:5000

Gerarchia:
  Fornitore  ->  Commessa (codice, opzionale)  ->  Bega
  Ogni bega puo' avere allegati PDF (anteprima nella scheda della bega).
"""

import csv
import io
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "beghe.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".pdf"}

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-cambiami-in-produzione"
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per upload

# Valori di catalogo: tenerli qui rende banale aggiungerne di nuovi.
CATEGORIE = ["Ritardo consegna", "Qualità", "Fatturazione", "Amministrativo", "Altro"]
PRIORITA = ["Bassa", "Media", "Alta"]
STATI = ["Aperta", "In lavorazione", "Risolta"]


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS fornitori (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome          TEXT NOT NULL,
            referente     TEXT,
            email         TEXT,
            telefono      TEXT,
            note          TEXT,
            creato_il     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS beghe (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            fornitore_id   INTEGER NOT NULL REFERENCES fornitori(id) ON DELETE CASCADE,
            titolo         TEXT NOT NULL,
            descrizione    TEXT,
            riferimenti    TEXT,
            categoria      TEXT,
            commessa       TEXT,
            priorita       TEXT NOT NULL DEFAULT 'Media',
            stato          TEXT NOT NULL DEFAULT 'Aperta',
            importo        REAL,
            data_apertura  TEXT NOT NULL DEFAULT (date('now')),
            data_consegna  TEXT,
            data_chiusura  TEXT,
            creato_il      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS allegati (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            bega_id       INTEGER NOT NULL REFERENCES beghe(id) ON DELETE CASCADE,
            nome_file     TEXT NOT NULL,
            file_salvato  TEXT NOT NULL,
            caricato_il   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    # Migrazione per DB esistenti: aggiunge la colonna 'commessa' se manca.
    colonne = [r[1] for r in db.execute("PRAGMA table_info(beghe)").fetchall()]
    if "commessa" not in colonne:
        db.execute("ALTER TABLE beghe ADD COLUMN commessa TEXT")
    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def riga_classe(bega, oggi):
    """Classe CSS in base alla scadenza: 'scaduta' | 'in-scadenza' | ''."""
    if bega["stato"] == "Risolta" or not bega["data_consegna"]:
        return ""
    try:
        scadenza = datetime.strptime(bega["data_consegna"], "%Y-%m-%d").date()
    except ValueError:
        return ""
    if scadenza < oggi:
        return "scaduta"
    if scadenza <= oggi + timedelta(days=3):
        return "in-scadenza"
    return ""


def colore_bega(bega, oggi):
    """Colore-stato del nodo: risolta | scaduta | in-scadenza | aperta."""
    if bega["stato"] == "Risolta":
        return "risolta"
    return riga_classe(bega, oggi) or "aperta"


def raggruppa_per_commessa(beghe, oggi):
    """Raggruppa una lista di righe-bega per commessa, preservando l'ordine."""
    gruppi = []
    indice = {}
    for b in beghe:
        chiave = b["commessa"] or ""
        if chiave not in indice:
            indice[chiave] = {"nome": b["commessa"], "beghe": []}
            gruppi.append(indice[chiave])
        riga = dict(b)
        riga["colore"] = colore_bega(b, oggi)
        indice[chiave]["beghe"].append(riga)
    for gr in gruppi:
        gr["aperte"] = sum(1 for x in gr["beghe"] if x["stato"] != "Risolta")
    return gruppi


@app.context_processor
def inject_globals():
    return {
        "CATEGORIE": CATEGORIE,
        "PRIORITA": PRIORITA,
        "STATI": STATI,
        "riga_classe": riga_classe,
        "oggi": date.today(),
    }


# --------------------------------------------------------------------------- #
# Rotte — Home (albero) / Fornitori
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    db = get_db()
    oggi = date.today()
    oggi_s = oggi.isoformat()
    tot = db.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE stato != 'Risolta')                          AS aperte,
            COUNT(*) FILTER (WHERE stato = 'In lavorazione')                    AS in_lavorazione,
            COUNT(*) FILTER (WHERE stato != 'Risolta'
                              AND data_consegna IS NOT NULL
                              AND data_consegna < ?)                            AS scadute,
            COUNT(*) FILTER (WHERE stato = 'Risolta')                          AS risolte
        FROM beghe
        """,
        (oggi_s,),
    ).fetchone()
    pdf_counts = {
        r["bega_id"]: r["n"]
        for r in db.execute("SELECT bega_id, COUNT(*) AS n FROM allegati GROUP BY bega_id")
    }
    fornitori = db.execute("SELECT * FROM fornitori ORDER BY nome").fetchall()
    alberi = []
    for f in fornitori:
        beghe = db.execute(
            """SELECT * FROM beghe WHERE fornitore_id = ?
               ORDER BY (commessa IS NULL), commessa,
                        (data_consegna IS NULL), data_consegna""",
            (f["id"],),
        ).fetchall()
        commesse = []
        for gr in raggruppa_per_commessa(beghe, oggi):
            commesse.append(
                {
                    "nome": gr["nome"] or "(nessuna commessa)",
                    "aperte": gr["aperte"],
                    "beghe": [
                        {
                            "id": b["id"],
                            "titolo": b["titolo"],
                            "descrizione": b["descrizione"],
                            "priorita": b["priorita"],
                            "stato": b["stato"],
                            "colore": b["colore"],
                            "has_pdf": pdf_counts.get(b["id"], 0) > 0,
                            "url": url_for("bega_detail", bid=b["id"]),
                        }
                        for b in gr["beghe"]
                    ],
                }
            )
        aperte = sum(c["aperte"] for c in commesse)
        alberi.append(
            {
                "id": f["id"],
                "nome": f["nome"],
                "url": url_for("fornitore", fid=f["id"]),
                "aperte": aperte,
                "commesse": commesse,
            }
        )
    return render_template("index.html", alberi=alberi, tot=tot)


@app.route("/fornitore/nuovo", methods=["POST"])
def fornitore_nuovo():
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        flash("Il nome del fornitore è obbligatorio.", "error")
        return redirect(url_for("index"))
    db = get_db()
    db.execute(
        "INSERT INTO fornitori (nome, referente, email, telefono, note) VALUES (?,?,?,?,?)",
        (
            nome,
            request.form.get("referente"),
            request.form.get("email"),
            request.form.get("telefono"),
            request.form.get("note"),
        ),
    )
    db.commit()
    flash(f"Fornitore «{nome}» aggiunto.", "ok")
    return redirect(url_for("index"))


@app.route("/fornitore/<int:fid>")
def fornitore(fid):
    db = get_db()
    f = db.execute("SELECT * FROM fornitori WHERE id = ?", (fid,)).fetchone()
    if f is None:
        flash("Fornitore non trovato.", "error")
        return redirect(url_for("index"))

    stato = request.args.get("stato") or ""
    priorita = request.args.get("priorita") or ""
    q = (request.args.get("q") or "").strip()

    sql = "SELECT * FROM beghe WHERE fornitore_id = ?"
    params = [fid]
    if stato:
        sql += " AND stato = ?"
        params.append(stato)
    if priorita:
        sql += " AND priorita = ?"
        params.append(priorita)
    if q:
        sql += " AND (titolo LIKE ? OR descrizione LIKE ? OR riferimenti LIKE ? OR commessa LIKE ?)"
        params += [f"%{q}%"] * 4
    sql += """
        ORDER BY (commessa IS NULL), commessa,
                 (stato = 'Risolta'),
                 (data_consegna IS NULL), data_consegna,
                 CASE priorita WHEN 'Alta' THEN 0 WHEN 'Media' THEN 1 ELSE 2 END
    """
    beghe = db.execute(sql, params).fetchall()
    commesse = raggruppa_per_commessa(beghe, date.today())

    return render_template(
        "fornitore.html",
        f=f,
        commesse=commesse,
        filtro={"stato": stato, "priorita": priorita, "q": q},
    )


@app.route("/fornitore/<int:fid>/elimina", methods=["POST"])
def fornitore_elimina(fid):
    db = get_db()
    # rimuove anche i file su disco degli allegati collegati
    for r in db.execute(
        """SELECT a.file_salvato FROM allegati a
           JOIN beghe b ON b.id = a.bega_id WHERE b.fornitore_id = ?""",
        (fid,),
    ).fetchall():
        _rimuovi_file(r["file_salvato"])
    db.execute("DELETE FROM fornitori WHERE id = ?", (fid,))
    db.commit()
    flash("Fornitore eliminato (con tutte le sue beghe).", "ok")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------- #
# Rotte — Beghe
# --------------------------------------------------------------------------- #
def _form_bega():
    """Estrae i campi della bega dal form, normalizzando i vuoti a None."""

    def clean(name):
        v = (request.form.get(name) or "").strip()
        return v or None

    return {
        "titolo": clean("titolo"),
        "descrizione": clean("descrizione"),
        "riferimenti": clean("riferimenti"),
        "categoria": clean("categoria"),
        "commessa": clean("commessa"),
        "priorita": request.form.get("priorita") or "Media",
        "stato": request.form.get("stato") or "Aperta",
        "data_consegna": clean("data_consegna"),
    }


def _salva_allegati(bid):
    """Salva i PDF caricati nel form (campo 'allegati') e ne registra i metadati."""
    db = get_db()
    salvati = 0
    for fs in request.files.getlist("allegati"):
        if not fs or not fs.filename:
            continue
        ext = os.path.splitext(fs.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            flash(f"«{fs.filename}» ignorato: sono ammessi solo PDF.", "error")
            continue
        nome_salvato = uuid.uuid4().hex + ext
        fs.save(UPLOAD_DIR / nome_salvato)
        db.execute(
            "INSERT INTO allegati (bega_id, nome_file, file_salvato) VALUES (?,?,?)",
            (bid, secure_filename(fs.filename) or "allegato.pdf", nome_salvato),
        )
        salvati += 1
    return salvati


def _rimuovi_file(nome_salvato):
    try:
        (UPLOAD_DIR / nome_salvato).unlink(missing_ok=True)
    except OSError:
        pass


@app.route("/bega/<int:bid>")
def bega_detail(bid):
    db = get_db()
    bega = db.execute("SELECT * FROM beghe WHERE id = ?", (bid,)).fetchone()
    if bega is None:
        flash("Bega non trovata.", "error")
        return redirect(url_for("index"))
    f = db.execute("SELECT * FROM fornitori WHERE id = ?", (bega["fornitore_id"],)).fetchone()
    allegati = db.execute(
        "SELECT * FROM allegati WHERE bega_id = ? ORDER BY caricato_il", (bid,)
    ).fetchall()
    return render_template("bega.html", b=bega, f=f, allegati=allegati)


@app.route("/fornitore/<int:fid>/bega/nuova", methods=["POST"])
def bega_nuova(fid):
    dati = _form_bega()
    if not dati["titolo"]:
        flash("Il titolo della bega è obbligatorio.", "error")
        return redirect(url_for("fornitore", fid=fid))
    db = get_db()
    chiusura = date.today().isoformat() if dati["stato"] == "Risolta" else None
    cur = db.execute(
        """INSERT INTO beghe
           (fornitore_id, titolo, descrizione, riferimenti, categoria, commessa,
            priorita, stato, data_consegna, data_chiusura)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            fid,
            dati["titolo"],
            dati["descrizione"],
            dati["riferimenti"],
            dati["categoria"],
            dati["commessa"],
            dati["priorita"],
            dati["stato"],
            dati["data_consegna"],
            chiusura,
        ),
    )
    bid = cur.lastrowid
    _salva_allegati(bid)
    db.commit()
    flash("Bega aggiunta.", "ok")
    return redirect(url_for("bega_detail", bid=bid))


@app.route("/bega/<int:bid>/modifica", methods=["POST"])
def bega_modifica(bid):
    db = get_db()
    bega = db.execute("SELECT * FROM beghe WHERE id = ?", (bid,)).fetchone()
    if bega is None:
        flash("Bega non trovata.", "error")
        return redirect(url_for("index"))
    dati = _form_bega()
    if not dati["titolo"]:
        flash("Il titolo della bega è obbligatorio.", "error")
        return redirect(url_for("bega_detail", bid=bid))
    if dati["stato"] == "Risolta":
        chiusura = bega["data_chiusura"] or date.today().isoformat()
    else:
        chiusura = None
    db.execute(
        """UPDATE beghe SET
             titolo=?, descrizione=?, riferimenti=?, categoria=?, commessa=?,
             priorita=?, stato=?, data_consegna=?, data_chiusura=?
           WHERE id=?""",
        (
            dati["titolo"],
            dati["descrizione"],
            dati["riferimenti"],
            dati["categoria"],
            dati["commessa"],
            dati["priorita"],
            dati["stato"],
            dati["data_consegna"],
            chiusura,
            bid,
        ),
    )
    _salva_allegati(bid)
    db.commit()
    flash("Bega aggiornata.", "ok")
    return redirect(url_for("bega_detail", bid=bid))


@app.route("/bega/<int:bid>/toggle", methods=["POST"])
def bega_toggle(bid):
    """Spunta/despunta velocemente: Risolta <-> Aperta."""
    db = get_db()
    bega = db.execute("SELECT * FROM beghe WHERE id = ?", (bid,)).fetchone()
    if bega is None:
        return redirect(url_for("index"))
    if bega["stato"] == "Risolta":
        nuovo, chiusura = "Aperta", None
    else:
        nuovo, chiusura = "Risolta", date.today().isoformat()
    db.execute(
        "UPDATE beghe SET stato=?, data_chiusura=? WHERE id=?",
        (nuovo, chiusura, bid),
    )
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/bega/<int:bid>/elimina", methods=["POST"])
def bega_elimina(bid):
    db = get_db()
    bega = db.execute("SELECT fornitore_id FROM beghe WHERE id = ?", (bid,)).fetchone()
    if bega is None:
        return redirect(url_for("index"))
    for r in db.execute("SELECT file_salvato FROM allegati WHERE bega_id = ?", (bid,)).fetchall():
        _rimuovi_file(r["file_salvato"])
    db.execute("DELETE FROM beghe WHERE id = ?", (bid,))
    db.commit()
    flash("Bega eliminata.", "ok")
    return redirect(url_for("fornitore", fid=bega["fornitore_id"]))


# --------------------------------------------------------------------------- #
# Rotte — Allegati
# --------------------------------------------------------------------------- #
@app.route("/allegato/<int:aid>")
def allegato_serve(aid):
    db = get_db()
    a = db.execute("SELECT * FROM allegati WHERE id = ?", (aid,)).fetchone()
    if a is None:
        abort(404)
    return send_from_directory(
        UPLOAD_DIR,
        a["file_salvato"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=a["nome_file"],
    )


@app.route("/allegato/<int:aid>/elimina", methods=["POST"])
def allegato_elimina(aid):
    db = get_db()
    a = db.execute("SELECT * FROM allegati WHERE id = ?", (aid,)).fetchone()
    if a is None:
        abort(404)
    _rimuovi_file(a["file_salvato"])
    db.execute("DELETE FROM allegati WHERE id = ?", (aid,))
    db.commit()
    flash("Allegato rimosso.", "ok")
    return redirect(url_for("bega_detail", bid=a["bega_id"]))


# --------------------------------------------------------------------------- #
# Mappa concettuale (fornitore -> commessa -> bega), senza beghe risolte
# --------------------------------------------------------------------------- #
@app.route("/mappa")
def mappa():
    db = get_db()
    oggi = date.today()
    fornitori = db.execute("SELECT * FROM fornitori ORDER BY nome").fetchall()
    grafo = []
    for f in fornitori:
        beghe = db.execute(
            """SELECT * FROM beghe
               WHERE fornitore_id = ? AND stato != 'Risolta'
               ORDER BY (commessa IS NULL), commessa,
                        (data_consegna IS NULL), data_consegna""",
            (f["id"],),
        ).fetchall()
        if not beghe:
            continue  # niente da mostrare per questo fornitore
        commesse = []
        for gr in raggruppa_per_commessa(beghe, oggi):
            commesse.append(
                {
                    "nome": gr["nome"] or "(nessuna commessa)",
                    "beghe": [
                        {
                            "id": b["id"],
                            "titolo": b["titolo"],
                            "stato": b["stato"],
                            "priorita": b["priorita"],
                            "categoria": b["categoria"],
                            "consegna": b["data_consegna"],
                            "colore": b["colore"],
                            "url": url_for("bega_detail", bid=b["id"]),
                        }
                        for b in gr["beghe"]
                    ],
                }
            )
        grafo.append(
            {
                "id": f["id"],
                "nome": f["nome"],
                "url": url_for("fornitore", fid=f["id"]),
                "commesse": commesse,
            }
        )
    return render_template("mappa.html", grafo=grafo)


# --------------------------------------------------------------------------- #
# Export CSV
# --------------------------------------------------------------------------- #
@app.route("/export.csv")
def export_csv():
    db = get_db()
    righe = db.execute(
        """
        SELECT f.nome AS fornitore, b.commessa, b.titolo, b.categoria, b.priorita, b.stato,
               b.data_apertura, b.data_consegna, b.data_chiusura, b.riferimenti, b.descrizione
        FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
        ORDER BY f.nome, b.commessa, b.data_consegna
        """
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "Fornitore", "Commessa", "Titolo", "Categoria", "Priorità", "Stato",
            "Data apertura", "Data consegna", "Data chiusura", "Riferimenti", "Descrizione",
        ]
    )
    for r in righe:
        w.writerow([r[k] if r[k] is not None else "" for k in r.keys()])
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=beghe.csv"},
    )


# Crea le tabelle all'import del modulo: necessario sotto WSGI
# (es. PythonAnywhere), dove il blocco __main__ non viene eseguito.
init_db()


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5000)), debug=True)
