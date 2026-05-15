# AV Assistant

Multi-chatbot configurabile per [Anonymous for the Voiceless](https://www.anonymousforthevoiceless.org/), costruito con Streamlit. Offre più "esperti" specializzati (nutrizionista, coach attivismo, training protocollo, coach in cucina) selezionabili dalla sidebar, con auth Google opzionale, persistenza chat su Firebase e supporto multilingua IT / EN.

🌐 **Live**: <https://av-assistant-jahevfahcjvxnt7sz5nnwk.streamlit.app/>

## Funzionalità

- **Multi-esperto** configurabile via JSON: ogni esperto ha icona, prompt di sistema, knowledge base, provider/modello LLM e disclaimer dedicati.
- **Multi-provider LLM**: supporto a Groq, Google Gemini e OpenRouter (per Claude, GPT, ecc.). Provider e modello sono scelti per esperto, con fallback globale.
- **Multilingua IT / EN** via parametro URL `?lang=IT` o selettore in sidebar.
- **Persistenza chat** anche per utenti anonimi (cookie `av_user_id` + Firestore).
- **Login Google** per esperti riservati (es. *Training Protocollo* visibile solo a profili `admin` / `org` / `activist`).
- **Pagine di gestione** (riservate a org/admin):
  - `I Miei Attivisti` — gestione attivisti del proprio capitolo.
  - `Gestione Organizzatori` — admin only, gestione capitoli e organizzatori.
  - `Chat Attivisti` — visualizzazione chat profilate.
- **Settings dinamici**: ogni esperto può dichiarare selectbox che vengono iniettati nel prompt (es. livello di difficoltà del passante simulato).

## Stack

- [Streamlit](https://streamlit.io/) (UI + auth + multipage)
- [Firebase Firestore](https://firebase.google.com/) (chat history + utenti)
- LLM: [Groq](https://groq.com/), [Google Gemini](https://ai.google.dev/), [OpenRouter](https://openrouter.ai/)
- Auth: Google OAuth via Authlib (`st.login`)

## Struttura del repository

```
.
├── app.py                       # Entry point: definisce navigazione e visibilità pagine
├── pages/
│   ├── chat.py                  # Chat principale (selezione esperto + LLM dispatch)
│   ├── 1_I_Miei_Attivisti.py
│   ├── 2_Gestione_Organizzatori.py
│   └── 3_Chat_Attivisti.py
├── data/
│   ├── experts.json             # Definizione degli esperti
│   ├── app_config.json          # Provider/modello di default
│   ├── i18n.json, ui.json       # Stringhe localizzate
│   └── kb_*.txt                 # Knowledge base testuali
├── prompts/                     # System prompt per esperto (.md)
├── assets/                      # Logo e immagini
├── .streamlit/
│   ├── config.toml              # Tema / config Streamlit
│   └── secrets.toml             # NON committato — vedi sotto
└── requirements.txt
```

## Setup locale

### 1. Clona il repo

```bash
git clone <repo-url>
cd av-bot-project
```

### 2. Configura `.streamlit/secrets.toml`

Crea il file con (almeno una API key + Firebase + auth Google se serve):

```toml
GROQ_API_KEY = "gsk_..."
GOOGLE_API_KEY = "AIza..."
OPENROUTER_API_KEY = "sk-or-..."

# --- Integrazione Telegram (gestione cubi) ---
# Da my.telegram.org/apps (vedi sotto)
TELEGRAM_API_ID = "12345"
TELEGRAM_API_HASH = "abcdef0123456789abcdef0123456789"
# Chiave Fernet per cifrare le session Telethon degli organizer.
# Generala UNA VOLTA con:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Non cambiarla mai dopo il primo deploy: cifra le session salvate.
TELEGRAM_SESSION_FERNET_KEY = "<44-char-base64-fernet-key>"

[firebase]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."

[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "<random string>"

[auth.google]
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

> Il file `secrets.toml` è già in `.gitignore`. **Non committarlo.**

### 3. Avvia

#### Windows (consigliato)

Usa lo script PowerShell [run.ps1](run.ps1) — gestisce automaticamente venv e dipendenze:

```powershell
.\run.ps1
```

Lo script:
1. crea `venv\` se non esiste (`python -m venv`);
2. attiva il venv nella sessione corrente se non già attivo;
3. installa/aggiorna le dipendenze **solo se `requirements.txt` è cambiato** (verifica via hash SHA256 salvato in `venv\.requirements.hash`);
4. lancia `streamlit run app.py`.

Opzioni:

```powershell
.\run.ps1 -ForceInstall   # forza il reinstall delle dipendenze
```

> Se PowerShell blocca lo script al primo avvio, esegui una volta:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> oppure lancia con `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

#### macOS / Linux (manuale)

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

L'app è disponibile su <http://localhost:8501>.

## Integrazione Telegram (gestione cubi)

Gli organizer possono collegare il proprio account Telegram per tracciare i sondaggi di partecipazione ai cubi nel gruppo del proprio capitolo e mandare reminder ai non-rispondenti. L'integrazione usa [Telethon](https://docs.telethon.dev/) (account utente via MTProto, non un bot): cosi' ogni organizer agisce con la propria identita' Telegram, gia' presente nei gruppi.

### Setup amministrativo (una volta sola, per il progetto)

1. **Ottieni `api_id` e `api_hash`** su [my.telegram.org/apps](https://my.telegram.org/apps):
   - Logga con un numero Telegram di tua scelta (puoi usare il tuo).
   - Vai su "API development tools".
   - Compila il form: App title `AV Assistant`, short name `av-assistant`, platform `Web`, descrizione libera.
   - Copia `api_id` (numero) e `api_hash` (stringa esadecimale).
2. **Genera la chiave Fernet** per cifrare le session degli organizer:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Salva l'output in un posto sicuro: **non va mai ruotata** una volta che ci sono session salvate (le invaliderebbe tutte).
3. Aggiungi i tre valori in `.streamlit/secrets.toml` (locale) e nei secrets di Streamlit Cloud.

### Onboarding di un organizer

L'organizer logga, va su "I miei attivisti" e apre l'expander **📡 Connetti il tuo Telegram**:
1. Inserisce numero di telefono in formato internazionale (es. `+39...`) e clicca "Invia codice".
2. Riceve un codice via Telegram, lo digita e clicca "Verifica codice".
3. Se ha la 2FA attiva, inserisce anche la password Telegram.
4. La session viene cifrata e salvata in Firestore (`organizers/{email}.telegram_session_encrypted`). Da quel momento il sistema puo' operare a suo nome su Telegram, finche' non revoca la sessione.

L'organizer puo' disconnettersi in qualunque momento cliccando "Disconnetti Telegram", oppure revocando "AV Assistant" da Telegram > Impostazioni > Dispositivi.

### Configurazione gruppo del capitolo

Stessa pagina, expander **📍 Gruppo Telegram del capitolo**: l'organizer inserisce l'`ID numerico` del gruppo Telegram dove vengono pubblicati i sondaggi dei cubi (di solito un valore negativo lungo, es. `-1001234567890`). Per ottenerlo si puo' usare un bot come `@userinfobot` aggiunto al gruppo, oppure copiare il link a un messaggio del gruppo e leggere l'id dall'URL.

## Aggiungere un nuovo esperto

1. Aggiungi una voce in [data/experts.json](data/experts.json):
   ```json
   {
     "id": "mio_esperto",
     "icon": "🎯",
     "label": { "IT": "Mio Esperto", "EN": "My Expert" },
     "prompt_file": "prompts/mio_esperto.md",
     "kb_file": null,
     "provider": "openrouter",
     "model_name": "anthropic/claude-sonnet-4.5"
   }
   ```
2. Crea [prompts/mio_esperto.md](prompts/) con il system prompt (puoi usare `{{LANGUAGE}}`).
3. *(Opzionale)* Aggiungi un file `kb_*.txt` in `data/` e referenzialo in `kb_file`.
4. *(Opzionale)* Restringi l'accesso aggiungendo `"authorizedProfiles": ["admin", "org"]`.

Niente modifiche al codice Python richieste.

## Profili utente

I profili sono salvati in Firestore in `users/{email}` come array `profiles`. Valori usati dall'app:

- `admin` — accesso completo (gestione organizzatori, tutti gli esperti riservati).
- `org` — gestione attivisti del proprio capitolo, accesso esperti riservati.
- `activist` — accesso a esperti di training (es. *Protocol Trainer*) e visualizzazione chat.

## Deploy

Il deploy è automatico su [Streamlit Community Cloud](https://streamlit.io/cloud): ogni push su `main` viene deployato live. I secret di produzione sono configurati nel pannello dell'app su Streamlit Cloud (non nel repo).

> ⚠️ Non c'è ambiente di staging: ogni commit su `main` va in produzione.

## Licenza / Crediti

Progetto interno per Anonymous for the Voiceless.
