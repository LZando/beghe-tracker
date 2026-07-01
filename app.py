"""
Beghe Tracker — checklist delle problematiche ("beghe") per fornitore.

Stack: Flask + SQLite (standard library + Werkzeug, gia' incluso con Flask).
Avvio:  python app.py   ->   http://127.0.0.1:5000

Gerarchia:
  Utente  ->  Fornitore  ->  Commessa (codice, opzionale)  ->  Bega
  Ogni bega puo' avere allegati PDF (anteprima nella scheda della bega),
  commenti cronologici e un utente assegnatario.

Accesso:
  I dati sono privati per utente: ognuno vede solo i propri fornitori/beghe.
  Una bega puo' essere assegnata a un altro utente registrato, che la vede
  (e puo' commentarla / cambiarne lo stato) senza vedere il resto.
"""

import csv
import io
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
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
    session,
    url_for,
)
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "beghe.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".pdf"}
MIN_PASSWORD = 6
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-cambiami-in-produzione")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per upload

# Valori di catalogo: tenerli qui rende banale aggiungerne di nuovi.
PRIORITA = ["Bassa", "Media", "Alta"]
STATI = ["Aperta", "In lavorazione", "Risolta"]

# Endpoint raggiungibili senza login.
PUBLIC_ENDPOINTS = {"login", "register", "static"}


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


BEGHE_SCHEMA = """
    CREATE TABLE {name} (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        fornitore_id   INTEGER NOT NULL REFERENCES fornitori(id) ON DELETE CASCADE,
        commessa       TEXT,
        descrizione    TEXT,
        azione         TEXT,
        riferimenti    TEXT,
        priorita       TEXT NOT NULL DEFAULT 'Media',
        stato          TEXT NOT NULL DEFAULT 'Aperta',
        assegnatario_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        data_apertura  TEXT NOT NULL DEFAULT (date('now')),
        data_consegna  TEXT,
        data_chiusura  TEXT,
        creato_il      TEXT NOT NULL DEFAULT (datetime('now'))
    )
"""


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        -- ATTENZIONE: password in CHIARO. Scelta temporanea da prototipo
        -- (richiesta esplicita). In produzione tornare a un hash (werkzeug
        -- generate_password_hash / check_password_hash).
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT NOT NULL UNIQUE,
            password      TEXT NOT NULL,
            nome          TEXT,
            creato_il     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS fornitori (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
            nome          TEXT NOT NULL,
            referente     TEXT,
            email         TEXT,
            telefono      TEXT,
            note          TEXT,
            creato_il     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS allegati (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            bega_id       INTEGER NOT NULL REFERENCES beghe(id) ON DELETE CASCADE,
            nome_file     TEXT NOT NULL,
            file_salvato  TEXT NOT NULL,
            caricato_il   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS commenti (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            bega_id       INTEGER NOT NULL REFERENCES beghe(id) ON DELETE CASCADE,
            user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
            testo         TEXT NOT NULL,
            creato_il     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    db.executescript(BEGHE_SCHEMA.format(name="IF NOT EXISTS beghe"))

    # ---- Migrazioni idempotenti per DB esistenti ----
    # users: se un vecchio DB aveva 'password_hash', ricostruisce la tabella con
    # la colonna 'password' in chiaro (l'hash non è reversibile: quegli account
    # dovranno reimpostare la password).
    user_cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "password" not in user_cols and "password_hash" in user_cols:
        db.executescript(
            """
            CREATE TABLE users_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                password      TEXT NOT NULL,
                nome          TEXT,
                creato_il     TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO users_new (id, email, password, nome, creato_il)
              SELECT id, email, password_hash, nome, creato_il FROM users;
            DROP TABLE users;
            ALTER TABLE users_new RENAME TO users;
            """
        )

    forn_cols = [r[1] for r in db.execute("PRAGMA table_info(fornitori)").fetchall()]
    if "owner_id" not in forn_cols:
        db.execute("ALTER TABLE fornitori ADD COLUMN owner_id INTEGER REFERENCES users(id)")

    cols = [r[1] for r in db.execute("PRAGMA table_info(beghe)").fetchall()]
    if "commessa" not in cols:
        db.execute("ALTER TABLE beghe ADD COLUMN commessa TEXT")
        cols.append("commessa")
    if "azione" not in cols:
        db.execute("ALTER TABLE beghe ADD COLUMN azione TEXT")
        cols.append("azione")
    if "assegnatario_id" not in cols:
        db.execute("ALTER TABLE beghe ADD COLUMN assegnatario_id INTEGER REFERENCES users(id)")
        cols.append("assegnatario_id")
    # Rimozione di titolo/categoria/importo: ricostruisce la tabella (compatibile
    # con ogni versione di SQLite). Il vecchio 'titolo' confluisce in 'descrizione'.
    if any(c in cols for c in ("titolo", "categoria", "importo")):
        desc = "COALESCE(NULLIF(descrizione, ''), titolo)" if "titolo" in cols else "descrizione"
        db.executescript(
            BEGHE_SCHEMA.format(name="beghe_new")
            + f"""
            ;INSERT INTO beghe_new
                (id, fornitore_id, commessa, descrizione, azione, riferimenti,
                 priorita, stato, assegnatario_id, data_apertura, data_consegna,
                 data_chiusura, creato_il)
              SELECT id, fornitore_id, commessa, {desc}, azione, riferimenti,
                     COALESCE(priorita, 'Media'), COALESCE(stato, 'Aperta'), assegnatario_id,
                     COALESCE(data_apertura, date('now')), data_consegna, data_chiusura,
                     COALESCE(creato_il, datetime('now'))
              FROM beghe;
            DROP TABLE beghe;
            ALTER TABLE beghe_new RENAME TO beghe;
            """
        )
    db.commit()
    db.close()


# --------------------------------------------------------------------------- #
# Autenticazione
# --------------------------------------------------------------------------- #
@app.before_request
def carica_utente():
    """Carica l'utente di sessione e blocca gli accessi non autenticati."""
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:  # utente cancellato: pulisci la sessione
            session.clear()

    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if g.user is None:
        nxt = request.full_path if request.method == "GET" else None
        return redirect(url_for("login", next=nxt))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def elenco_utenti():
    """Utenti registrati, per il menu «assegnatario»."""
    return get_db().execute(
        "SELECT id, nome, email FROM users ORDER BY COALESCE(NULLIF(nome,''), email)"
    ).fetchall()


def nome_utente(u):
    """Etichetta leggibile di un utente-row (nome se c'è, altrimenti email)."""
    return (u["nome"] or "").strip() or u["email"]


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pwd = request.form.get("password") or ""
        pwd2 = request.form.get("password2") or ""
        nome = (request.form.get("nome") or "").strip()

        errors = []
        if not EMAIL_RE.match(email):
            errors.append("Inserisci un indirizzo email valido.")
        if len(pwd) < MIN_PASSWORD:
            errors.append(f"La password deve avere almeno {MIN_PASSWORD} caratteri.")
        if pwd != pwd2:
            errors.append("Le due password non coincidono.")
        db = get_db()
        if not errors and db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("Esiste già un account con questa email.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html", email=email, nome=nome)

        cur = db.execute(
            "INSERT INTO users (email, password, nome) VALUES (?,?,?)",
            (email, pwd, nome or None),  # password in chiaro (fase prototipo)
        )
        uid = cur.lastrowid
        # Il primo utente registrato eredita i dati storici senza proprietario.
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
            db.execute("UPDATE fornitori SET owner_id = ? WHERE owner_id IS NULL", (uid,))
        db.commit()
        session.clear()
        session["uid"] = uid
        flash("Benvenuto! Account creato.", "ok")
        return redirect(url_for("index"))
    return render_template("register.html", email="", nome="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        pwd = request.form.get("password") or ""
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or user["password"] != pwd:  # confronto in chiaro (fase prototipo)
            flash("Email o password non corretti.", "error")
            return render_template("login.html", email=email)
        session.clear()
        session["uid"] = user["id"]
        nxt = request.form.get("next") or ""
        # Evita open-redirect: accetta solo path interni.
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for("index"))
    return render_template("login.html", email="", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sei uscito.", "ok")
    return redirect(url_for("login"))


@app.route("/profilo", methods=["GET", "POST"])
@login_required
def profilo():
    db = get_db()
    if request.method == "POST":
        azione = request.form.get("azione")
        if azione == "nome":
            nome = (request.form.get("nome") or "").strip()
            db.execute("UPDATE users SET nome = ? WHERE id = ?", (nome or None, g.user["id"]))
            db.commit()
            flash("Profilo aggiornato.", "ok")
        elif azione == "password":
            attuale = request.form.get("password_attuale") or ""
            nuova = request.form.get("password_nuova") or ""
            nuova2 = request.form.get("password_nuova2") or ""
            if g.user["password"] != attuale:  # confronto in chiaro (fase prototipo)
                flash("La password attuale non è corretta.", "error")
            elif len(nuova) < MIN_PASSWORD:
                flash(f"La nuova password deve avere almeno {MIN_PASSWORD} caratteri.", "error")
            elif nuova != nuova2:
                flash("Le due nuove password non coincidono.", "error")
            else:
                db.execute(
                    "UPDATE users SET password = ? WHERE id = ?",
                    (nuova, g.user["id"]),
                )
                db.commit()
                flash("Password aggiornata.", "ok")
        return redirect(url_for("profilo"))

    stats = db.execute(
        """SELECT
             (SELECT COUNT(*) FROM fornitori WHERE owner_id = ?)                       AS fornitori,
             (SELECT COUNT(*) FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
              WHERE f.owner_id = ?)                                                    AS beghe,
             (SELECT COUNT(*) FROM beghe WHERE assegnatario_id = ?)                    AS assegnate
        """,
        (g.user["id"], g.user["id"], g.user["id"]),
    ).fetchone()
    return render_template("profilo.html", stats=stats)


# --------------------------------------------------------------------------- #
# Helper — accesso ai dati con scoping per proprietario
# --------------------------------------------------------------------------- #
def fornitore_posseduto(fid):
    """Fornitore se appartiene all'utente corrente, altrimenti None."""
    return get_db().execute(
        "SELECT * FROM fornitori WHERE id = ? AND owner_id = ?", (fid, g.user["id"])
    ).fetchone()


def bega_visibile(bid):
    """Bega se l'utente ne è proprietario (del fornitore) o assegnatario."""
    return get_db().execute(
        """SELECT b.* FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
           WHERE b.id = ? AND (f.owner_id = ? OR b.assegnatario_id = ?)""",
        (bid, g.user["id"], g.user["id"]),
    ).fetchone()


def is_owner_bega(bega):
    """True se l'utente possiede il fornitore della bega (permessi pieni)."""
    f = get_db().execute(
        "SELECT owner_id FROM fornitori WHERE id = ?", (bega["fornitore_id"],)
    ).fetchone()
    return f is not None and f["owner_id"] == g.user["id"]


# --------------------------------------------------------------------------- #
# Helper — presentazione
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
        "PRIORITA": PRIORITA,
        "STATI": STATI,
        "riga_classe": riga_classe,
        "nome_utente": nome_utente,
        "user": g.get("user"),
        "oggi": date.today(),
    }


# --------------------------------------------------------------------------- #
# Rotte — Home (albero) / Fornitori
# --------------------------------------------------------------------------- #
@app.route("/")
@login_required
def index():
    db = get_db()
    uid = g.user["id"]
    oggi = date.today()
    oggi_s = oggi.isoformat()
    tot = db.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE b.stato != 'Risolta')                        AS aperte,
            COUNT(*) FILTER (WHERE b.stato = 'In lavorazione')                  AS in_lavorazione,
            COUNT(*) FILTER (WHERE b.stato != 'Risolta'
                              AND b.data_consegna IS NOT NULL
                              AND b.data_consegna < ?)                          AS scadute,
            COUNT(*) FILTER (WHERE b.stato = 'Risolta')                        AS risolte
        FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
        WHERE f.owner_id = ? OR b.assegnatario_id = ?
        """,
        (oggi_s, uid, uid),
    ).fetchone()
    pdf_counts = {
        r["bega_id"]: r["n"]
        for r in db.execute("SELECT bega_id, COUNT(*) AS n FROM allegati GROUP BY bega_id")
    }
    fornitori = db.execute(
        "SELECT * FROM fornitori WHERE owner_id = ? ORDER BY nome", (uid,)
    ).fetchall()
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
                            "descrizione": b["descrizione"],
                            "azione": b["azione"],
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

    # Promemoria scadenze: beghe visibili, non risolte, con consegna imminente/passata.
    urgenti = db.execute(
        """SELECT b.*, f.nome AS fornitore_nome
           FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
           WHERE (f.owner_id = ? OR b.assegnatario_id = ?)
             AND b.stato != 'Risolta' AND b.data_consegna IS NOT NULL
           ORDER BY b.data_consegna""",
        (uid, uid),
    ).fetchall()
    scadute, in_scadenza = [], []
    for b in urgenti:
        cl = riga_classe(b, oggi)
        if cl == "scaduta":
            scadute.append(b)
        elif cl == "in-scadenza":
            in_scadenza.append(b)

    # Beghe assegnate a me su fornitori di altri utenti.
    assegnate = db.execute(
        """SELECT b.*, f.nome AS fornitore_nome
           FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
           WHERE b.assegnatario_id = ? AND f.owner_id != ? AND b.stato != 'Risolta'
           ORDER BY (b.data_consegna IS NULL), b.data_consegna""",
        (uid, uid),
    ).fetchall()

    return render_template(
        "index.html",
        alberi=alberi,
        tot=tot,
        scadute=scadute,
        in_scadenza=in_scadenza,
        assegnate=assegnate,
    )


@app.route("/fornitore/nuovo", methods=["POST"])
@login_required
def fornitore_nuovo():
    nome = (request.form.get("nome") or "").strip()
    if not nome:
        flash("Il nome del fornitore è obbligatorio.", "error")
        return redirect(url_for("index"))
    db = get_db()
    db.execute(
        """INSERT INTO fornitori (owner_id, nome, referente, email, telefono, note)
           VALUES (?,?,?,?,?,?)""",
        (
            g.user["id"],
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
@login_required
def fornitore(fid):
    f = fornitore_posseduto(fid)
    if f is None:
        flash("Fornitore non trovato.", "error")
        return redirect(url_for("index"))
    db = get_db()

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
        sql += " AND (descrizione LIKE ? OR azione LIKE ? OR riferimenti LIKE ? OR commessa LIKE ?)"
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
        utenti=elenco_utenti(),
    )


@app.route("/fornitore/<int:fid>/elimina", methods=["POST"])
@login_required
def fornitore_elimina(fid):
    if fornitore_posseduto(fid) is None:
        flash("Fornitore non trovato.", "error")
        return redirect(url_for("index"))
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
def _assegnatario_valido(raw):
    """Ritorna un user_id esistente oppure None."""
    if not raw:
        return None
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        return None
    row = get_db().execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone()
    return uid if row else None


def _form_bega():
    """Estrae i campi della bega dal form, normalizzando i vuoti a None."""

    def clean(name):
        v = (request.form.get(name) or "").strip()
        return v or None

    return {
        "commessa": clean("commessa"),
        "descrizione": clean("descrizione"),
        "azione": clean("azione"),
        "riferimenti": clean("riferimenti"),
        "priorita": request.form.get("priorita") or "Media",
        "stato": request.form.get("stato") or "Aperta",
        "assegnatario_id": _assegnatario_valido(request.form.get("assegnatario_id")),
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
@login_required
def bega_detail(bid):
    bega = bega_visibile(bid)
    if bega is None:
        flash("Bega non trovata.", "error")
        return redirect(url_for("index"))
    db = get_db()
    f = db.execute("SELECT * FROM fornitori WHERE id = ?", (bega["fornitore_id"],)).fetchone()
    allegati = db.execute(
        "SELECT * FROM allegati WHERE bega_id = ? ORDER BY caricato_il", (bid,)
    ).fetchall()
    commenti = db.execute(
        """SELECT c.*, u.nome AS autore_nome, u.email AS autore_email
           FROM commenti c LEFT JOIN users u ON u.id = c.user_id
           WHERE c.bega_id = ? ORDER BY c.creato_il""",
        (bid,),
    ).fetchall()
    assegnatario = None
    if bega["assegnatario_id"]:
        assegnatario = db.execute(
            "SELECT id, nome, email FROM users WHERE id = ?", (bega["assegnatario_id"],)
        ).fetchone()
    return render_template(
        "bega.html",
        b=bega,
        f=f,
        allegati=allegati,
        commenti=commenti,
        assegnatario=assegnatario,
        is_owner=is_owner_bega(bega),
        utenti=elenco_utenti(),
    )


@app.route("/fornitore/<int:fid>/bega/nuova", methods=["POST"])
@login_required
def bega_nuova(fid):
    if fornitore_posseduto(fid) is None:
        flash("Fornitore non trovato.", "error")
        return redirect(url_for("index"))
    dati = _form_bega()
    if not dati["descrizione"]:
        flash("La descrizione della bega è obbligatoria.", "error")
        return redirect(url_for("fornitore", fid=fid))
    db = get_db()
    chiusura = date.today().isoformat() if dati["stato"] == "Risolta" else None
    cur = db.execute(
        """INSERT INTO beghe
           (fornitore_id, commessa, descrizione, azione, riferimenti,
            priorita, stato, assegnatario_id, data_consegna, data_chiusura)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            fid,
            dati["commessa"],
            dati["descrizione"],
            dati["azione"],
            dati["riferimenti"],
            dati["priorita"],
            dati["stato"],
            dati["assegnatario_id"],
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
@login_required
def bega_modifica(bid):
    bega = bega_visibile(bid)
    if bega is None:
        flash("Bega non trovata.", "error")
        return redirect(url_for("index"))
    if not is_owner_bega(bega):
        flash("Solo il proprietario del fornitore può modificare la bega.", "error")
        return redirect(url_for("bega_detail", bid=bid))
    dati = _form_bega()
    if not dati["descrizione"]:
        flash("La descrizione della bega è obbligatoria.", "error")
        return redirect(url_for("bega_detail", bid=bid))
    if dati["stato"] == "Risolta":
        chiusura = bega["data_chiusura"] or date.today().isoformat()
    else:
        chiusura = None
    db = get_db()
    db.execute(
        """UPDATE beghe SET
             commessa=?, descrizione=?, azione=?, riferimenti=?,
             priorita=?, stato=?, assegnatario_id=?, data_consegna=?, data_chiusura=?
           WHERE id=?""",
        (
            dati["commessa"],
            dati["descrizione"],
            dati["azione"],
            dati["riferimenti"],
            dati["priorita"],
            dati["stato"],
            dati["assegnatario_id"],
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
@login_required
def bega_toggle(bid):
    """Spunta/despunta velocemente: Risolta <-> Aperta. Owner o assegnatario."""
    bega = bega_visibile(bid)
    if bega is None:
        return redirect(url_for("index"))
    if bega["stato"] == "Risolta":
        nuovo, chiusura = "Aperta", None
    else:
        nuovo, chiusura = "Risolta", date.today().isoformat()
    db = get_db()
    db.execute(
        "UPDATE beghe SET stato=?, data_chiusura=? WHERE id=?",
        (nuovo, chiusura, bid),
    )
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/bega/<int:bid>/commento", methods=["POST"])
@login_required
def bega_commento(bid):
    """Aggiunge un commento cronologico. Owner o assegnatario."""
    bega = bega_visibile(bid)
    if bega is None:
        flash("Bega non trovata.", "error")
        return redirect(url_for("index"))
    testo = (request.form.get("testo") or "").strip()
    if not testo:
        flash("Il commento è vuoto.", "error")
        return redirect(url_for("bega_detail", bid=bid))
    db = get_db()
    db.execute(
        "INSERT INTO commenti (bega_id, user_id, testo) VALUES (?,?,?)",
        (bid, g.user["id"], testo),
    )
    db.commit()
    flash("Commento aggiunto.", "ok")
    return redirect(url_for("bega_detail", bid=bid))


@app.route("/commento/<int:cid>/elimina", methods=["POST"])
@login_required
def commento_elimina(cid):
    db = get_db()
    c = db.execute("SELECT * FROM commenti WHERE id = ?", (cid,)).fetchone()
    if c is None:
        abort(404)
    bega = bega_visibile(c["bega_id"])
    # Puo' eliminare l'autore del commento o il proprietario del fornitore.
    if bega is None or (c["user_id"] != g.user["id"] and not is_owner_bega(bega)):
        flash("Non puoi eliminare questo commento.", "error")
        return redirect(url_for("bega_detail", bid=c["bega_id"]))
    db.execute("DELETE FROM commenti WHERE id = ?", (cid,))
    db.commit()
    flash("Commento eliminato.", "ok")
    return redirect(url_for("bega_detail", bid=c["bega_id"]))


@app.route("/bega/<int:bid>/elimina", methods=["POST"])
@login_required
def bega_elimina(bid):
    bega = bega_visibile(bid)
    if bega is None:
        return redirect(url_for("index"))
    if not is_owner_bega(bega):
        flash("Solo il proprietario del fornitore può eliminare la bega.", "error")
        return redirect(url_for("bega_detail", bid=bid))
    db = get_db()
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
@login_required
def allegato_serve(aid):
    db = get_db()
    a = db.execute("SELECT * FROM allegati WHERE id = ?", (aid,)).fetchone()
    if a is None or bega_visibile(a["bega_id"]) is None:
        abort(404)
    return send_from_directory(
        UPLOAD_DIR,
        a["file_salvato"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=a["nome_file"],
    )


@app.route("/allegato/<int:aid>/elimina", methods=["POST"])
@login_required
def allegato_elimina(aid):
    db = get_db()
    a = db.execute("SELECT * FROM allegati WHERE id = ?", (aid,)).fetchone()
    if a is None:
        abort(404)
    bega = bega_visibile(a["bega_id"])
    if bega is None or not is_owner_bega(bega):
        flash("Non puoi rimuovere questo allegato.", "error")
        return redirect(url_for("bega_detail", bid=a["bega_id"]))
    _rimuovi_file(a["file_salvato"])
    db.execute("DELETE FROM allegati WHERE id = ?", (aid,))
    db.commit()
    flash("Allegato rimosso.", "ok")
    return redirect(url_for("bega_detail", bid=a["bega_id"]))


# --------------------------------------------------------------------------- #
# Mappa concettuale (fornitore -> commessa -> bega), senza beghe risolte
# --------------------------------------------------------------------------- #
@app.route("/mappa")
@login_required
def mappa():
    db = get_db()
    oggi = date.today()
    fornitori = db.execute(
        "SELECT * FROM fornitori WHERE owner_id = ? ORDER BY nome", (g.user["id"],)
    ).fetchall()
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
                            "descrizione": b["descrizione"],
                            "azione": b["azione"],
                            "stato": b["stato"],
                            "priorita": b["priorita"],
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
@login_required
def export_csv():
    db = get_db()
    righe = db.execute(
        """
        SELECT f.nome AS fornitore, b.commessa, b.descrizione, b.azione, b.priorita, b.stato,
               b.data_apertura, b.data_consegna, b.data_chiusura, b.riferimenti
        FROM beghe b JOIN fornitori f ON f.id = b.fornitore_id
        WHERE f.owner_id = ?
        ORDER BY f.nome, b.commessa, b.data_consegna
        """,
        (g.user["id"],),
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "Fornitore", "Commessa", "Descrizione", "Azione", "Priorità", "Stato",
            "Data apertura", "Data consegna", "Data chiusura", "Riferimenti",
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
