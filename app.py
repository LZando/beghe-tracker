"""
Beghe Tracker — checklist delle problematiche ("beghe") per fornitore.

Stack: Flask + SQLite (standard library, nessuna dipendenza extra).
Avvio:  python app.py   ->   http://127.0.0.1:5000

Struttura:
  - Fornitore: anagrafica + referente.
  - Bega: problematica aperta verso un fornitore (descrizione, riferimenti,
    categoria, priorita, stato, data consegna ordine).
  - La riga diventa ROSSA se la data di consegna e' passata e la bega
    non e' ancora risolta; GIALLA se scade entro 3 giorni.
"""

import csv
import io
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "beghe.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-cambiami-in-produzione"

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
            priorita       TEXT NOT NULL DEFAULT 'Media',
            stato          TEXT NOT NULL DEFAULT 'Aperta',
            importo        REAL,
            data_apertura  TEXT NOT NULL DEFAULT (date('now')),
            data_consegna  TEXT,
            data_chiusura  TEXT,
            creato_il      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def riga_classe(bega, oggi):
    """Restituisce la classe CSS della riga in base alla scadenza."""
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
# Rotte — Dashboard / Fornitori
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    db = get_db()
    oggi = date.today().isoformat()
    fornitori = db.execute(
        """
        SELECT f.*,
               COUNT(b.id) FILTER (WHERE b.stato != 'Risolta')                         AS aperte,
               COUNT(b.id) FILTER (WHERE b.stato != 'Risolta'
                                     AND b.data_consegna IS NOT NULL
                                     AND b.data_consegna < ?)                           AS scadute
        FROM fornitori f
        LEFT JOIN beghe b ON b.fornitore_id = f.id
        GROUP BY f.id
        ORDER BY scadute DESC, aperte DESC, f.nome
        """,
        (oggi,),
    ).fetchall()

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
        (oggi,),
    ).fetchone()

    return render_template("index.html", fornitori=fornitori, tot=tot)


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
        sql += " AND (titolo LIKE ? OR descrizione LIKE ? OR riferimenti LIKE ?)"
        params += [f"%{q}%"] * 3
    # Ordine: prima le non risolte, poi per scadenza piu vicina, poi priorita.
    sql += """
        ORDER BY (stato = 'Risolta'),
                 (data_consegna IS NULL),
                 data_consegna,
                 CASE priorita WHEN 'Alta' THEN 0 WHEN 'Media' THEN 1 ELSE 2 END
    """
    beghe = db.execute(sql, params).fetchall()

    return render_template(
        "fornitore.html",
        f=f,
        beghe=beghe,
        filtro={"stato": stato, "priorita": priorita, "q": q},
    )


@app.route("/fornitore/<int:fid>/elimina", methods=["POST"])
def fornitore_elimina(fid):
    db = get_db()
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

    importo = clean("importo")
    try:
        importo = float(importo.replace(",", ".")) if importo else None
    except ValueError:
        importo = None

    return {
        "titolo": clean("titolo"),
        "descrizione": clean("descrizione"),
        "riferimenti": clean("riferimenti"),
        "categoria": clean("categoria"),
        "priorita": request.form.get("priorita") or "Media",
        "stato": request.form.get("stato") or "Aperta",
        "importo": importo,
        "data_consegna": clean("data_consegna"),
    }


@app.route("/fornitore/<int:fid>/bega/nuova", methods=["POST"])
def bega_nuova(fid):
    dati = _form_bega()
    if not dati["titolo"]:
        flash("Il titolo della bega è obbligatorio.", "error")
        return redirect(url_for("fornitore", fid=fid))
    db = get_db()
    chiusura = date.today().isoformat() if dati["stato"] == "Risolta" else None
    db.execute(
        """INSERT INTO beghe
           (fornitore_id, titolo, descrizione, riferimenti, categoria,
            priorita, stato, importo, data_consegna, data_chiusura)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            fid,
            dati["titolo"],
            dati["descrizione"],
            dati["riferimenti"],
            dati["categoria"],
            dati["priorita"],
            dati["stato"],
            dati["importo"],
            dati["data_consegna"],
            chiusura,
        ),
    )
    db.commit()
    flash("Bega aggiunta.", "ok")
    return redirect(url_for("fornitore", fid=fid))


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
        return redirect(url_for("fornitore", fid=bega["fornitore_id"]))
    # Imposta/azzera la data di chiusura quando lo stato passa a/da Risolta.
    if dati["stato"] == "Risolta":
        chiusura = bega["data_chiusura"] or date.today().isoformat()
    else:
        chiusura = None
    db.execute(
        """UPDATE beghe SET
             titolo=?, descrizione=?, riferimenti=?, categoria=?,
             priorita=?, stato=?, importo=?, data_consegna=?, data_chiusura=?
           WHERE id=?""",
        (
            dati["titolo"],
            dati["descrizione"],
            dati["riferimenti"],
            dati["categoria"],
            dati["priorita"],
            dati["stato"],
            dati["importo"],
            dati["data_consegna"],
            chiusura,
            bid,
        ),
    )
    db.commit()
    flash("Bega aggiornata.", "ok")
    return redirect(url_for("fornitore", fid=bega["fornitore_id"]))


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
    return redirect(url_for("fornitore", fid=bega["fornitore_id"]))


@app.route("/bega/<int:bid>/elimina", methods=["POST"])
def bega_elimina(bid):
    db = get_db()
    bega = db.execute("SELECT fornitore_id FROM beghe WHERE id = ?", (bid,)).fetchone()
    if bega is None:
        return redirect(url_for("index"))
    db.execute("DELETE FROM beghe WHERE id = ?", (bid,))
    db.commit()
    flash("Bega eliminata.", "ok")
    return redirect(url_for("fornitore", fid=bega["fornitore_id"]))


# --------------------------------------------------------------------------- #
# Export CSV
# --------------------------------------------------------------------------- #
@app.route("/export.csv")
def export_csv():
    db = get_db()
    righe = db.execute(
        """
        SELECT f.nome AS fornitore, b.titolo, b.categoria, b.priorita, b.stato,
               b.importo, b.data_apertura, b.data_consegna, b.data_chiusura,
               b.riferimenti, b.descrizione
        FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
        ORDER BY f.nome, b.data_consegna
        """
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "Fornitore", "Titolo", "Categoria", "Priorità", "Stato", "Importo",
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
# E' idempotente: usa CREATE TABLE IF NOT EXISTS.
init_db()


if __name__ == "__main__":
    app.run(debug=True)
