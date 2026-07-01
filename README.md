# Beghe Tracker

Checklist delle problematiche ("beghe") aperte verso i fornitori.
Prototipo interno per ordinare le idee prima di passare la palla all'IT.

## Cosa fa

- **Account & login**: registrazione libera (email + password ≥ 6 caratteri).
  I dati sono **privati per utente**: ognuno vede solo i propri fornitori/beghe.
- **Fornitori** con anagrafica + referente (nome, email, telefono, note).
- **Beghe** per ogni fornitore: titolo, descrizione, riferimenti (ordini/DDT/fatture/link),
  categoria, priorità, stato, importo e **data di consegna ordine**.
- **Assegnatario**: assegna una bega a un altro utente registrato; lui la vede
  nella sua dashboard («Assegnate a me»), può cambiarne lo stato e commentarla.
- **Commenti**: log cronologico di note per ogni bega (autore + data).
- **Promemoria scadenze**: sezione in dashboard con le beghe scadute / in scadenza.
- **Profilo**: cambio nome e password.
- **Checklist**: spunta una bega per segnarla risolta (un click).
- **Colore riga**: rossa se la consegna è scaduta e la bega non è risolta,
  gialla se scade entro 3 giorni.
- **Dashboard** con i totali (aperte, in lavorazione, scadute, risolte) e i fornitori
  ordinati per urgenza.
- **Filtri** per stato/priorità + ricerca testo.
- **Export CSV** (apribile in Excel).

## Avvio

```bash
# 1. (opzionale) crea/attiva il virtualenv già presente: .venv
# 2. installa Flask
pip install -r requirements.txt
# 3. avvia
python app.py
```

Apri http://127.0.0.1:5000

Il database SQLite (`beghe.db`) viene creato automaticamente al primo avvio,
nella cartella del progetto.

## Struttura

```
app.py                 # tutta la logica (rotte + accesso DB)
templates/
  base.html            # layout
  index.html           # dashboard + lista fornitori
  fornitore.html       # dettaglio fornitore con le sue beghe
static/style.css       # stile
beghe.db               # database SQLite (creato al primo avvio)
```

In produzione imposta la variabile d'ambiente `SECRET_KEY` (firma i cookie di
sessione); in locale usa un default di sviluppo.

## Note per l'IT (idee future)

- Notifiche email sulle scadenze (ora il promemoria è solo in-app).
- Reset password via email (ora si cambia solo dal profilo, da loggati).
- Stati personalizzabili e categorie gestibili da UI.
- Migrazione a Postgres per carichi maggiori.
