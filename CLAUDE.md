# CLAUDE.md

Guida per Claude Code per lavorare su questo progetto. Il progetto è di **Anonymous for the Voiceless (AV)**: un multi-chatbot Streamlit configurabile, multilingua (IT/EN), con auth Google e backend Firebase.

## Architettura in breve

- **Entry point**: [app.py](app.py) — definisce le `st.Page` e la navigazione condizionata dai profili utente. Le pagine in [pages/](pages/) **non** sono auto-discoverate: la sidebar nativa di Streamlit è nascosta via CSS (`stSidebarNav { display: none }`) e l'unica navigazione visibile è quella costruita in `app.py`.
- **Chat principale**: [pages/chat.py](pages/chat.py) — un unico file che gestisce caricamento config, cookie utente, login Google, selezione esperto, rendering settings, costruzione system prompt, dispatch del provider LLM (Groq / Gemini / OpenRouter), streaming risposta, persistenza Firestore. Quando si aggiunge una feature alla chat, **quasi sempre si tocca questo file**.
- **Pagine admin/org** ([pages/1_I_Miei_Attivisti.py](pages/1_I_Miei_Attivisti.py), [pages/2_Gestione_Organizzatori.py](pages/2_Gestione_Organizzatori.py), [pages/3_Chat_Attivisti.py](pages/3_Chat_Attivisti.py), [pages/4_Gestione_Cubi.py](pages/4_Gestione_Cubi.py)) — gestione utenti / capitoli / visualizzazione chat profilate / gestione eventi cubo. Visibili solo a profili `org` / `admin` / `activist` (vedi `app.py` per la logica).

## Configurazione (data-driven)

Tutto ciò che riguarda gli "esperti" (chatbot) è dichiarativo:

- **[data/experts.json](data/experts.json)** — lista degli esperti. Campi: `id`, `icon`, `label{IT,EN}`, `prompt_file`, `kb_file`, `provider` (`groq`/`gemini`/`openrouter`), `model_name`, `disclaimer{IT,EN}`, `settings[]` (selectbox dinamici iniettati nel prompt come `{{key}}`), `authorizedProfiles[]` (filtra visibilità).
- **[data/app_config.json](data/app_config.json)** — provider/model di default + temperature (fallback se l'expert non li specifica).
- **[data/i18n.json](data/i18n.json)** — stringhe interfaccia menu / auth.
- **[data/ui.json](data/ui.json)** — stringhe UI chat.
- **[prompts/*.md](prompts/)** — system prompt per esperto. Supportano `{{LANGUAGE}}` e i placeholder dei `settings`.
- **[data/kb_*.txt](data/)** — knowledge base testuali appese al system prompt.

**Aggiungere un nuovo esperto**: solo aggiungere voce in `experts.json` + creare il prompt in `prompts/`. Niente codice.

## Firebase / Firestore

Init in `pages/chat.py` da `st.secrets["firebase"]`. Collezioni:
- `users/{email}` — `{ profiles: [...], nome, cognome, ... }`. Profili noti: `admin`, `org`, `activist`. Eventuali campi futuri: `telegram_user_id`, `telegram_username` (popolati lazy in Fase 3 se servono).
- `active_chats/{user_id}` — chat live per cookie `av_user_id` (UUID per utente, anche anonimo).
- `profiled_chats/{chat_id}` — log permanente (solo per esperti con `authorizedProfiles` non vuoto + utente loggato).
- `organizers/{org_email}` — `{ activists: [{nome, cognome, email, data_ingresso, telefono, provincia, note, telegram_username}, ...], telegram_session_encrypted, telegram_chat_id, telegram_chat_title }`. La collezione modella anche il "capitolo" implicito: l'array `activists` sono gli attivisti del capitolo gestito da quell'organizer; i campi `telegram_*` configurano l'integrazione Telegram del capitolo (vedi sezione apposita).
- `cube_events/{event_id}` — un cubo (evento) creato da un organizer. Schema: `{ id, organizer_email, data, ora, luogo, note, poll_link, telegram_chat_ref, telegram_poll_msg_id, poll_question, poll_options: [{idx, text, category: yes|maybe|no|ignore}], poll_multiple_choice, participations: { email: {voted, option_idx, option_text, telegram_user_id} }, outside_voters: [{user_id, username, first_name, last_name, option_idx, option_text}], last_refresh, created_at, updated_at, status: active|closed }`. L'organizer crea il sondaggio in Telegram a mano, incolla il link al messaggio nel form di creazione cubo; la pagina chiama `get_poll_message` per validare e poi `get_poll_voters` su richiesta per popolare `participations`/`outside_voters`. La lista degli attivisti invitati e' dinamica (sempre ricalcolata dal documento `organizers/{email}.activists` corrente al momento del rendering).

## Integrazione Telegram (Cubi)

Feature per aiutare gli organizer a tracciare i sondaggi di partecipazione ai cubi nei loro gruppi Telegram e a sollecitare i non-rispondenti.

**Architettura**: usiamo [Telethon](https://docs.telethon.dev/) (MTProto, account utente, non bot) per due ragioni: 1) leggere lo stato corrente di un sondaggio retroattivamente, cosa non possibile via Bot API senza webhook; 2) usare l'account Telegram di ciascun organizer (che e' gia' nei suoi gruppi) invece di un bot da aggiungere ovunque.

**Modello dati Telegram-related**:
- `organizers/{email}.telegram_session_encrypted` — `StringSession` Telethon cifrata con Fernet (chiave in `st.secrets`). Permette di agire come quell'organizer su Telegram.
- `organizers/{email}.telegram_chat_id` / `telegram_chat_title` — gruppo Telegram del capitolo.
- `organizers/{email}.activists[i].telegram_username` — handle Telegram dell'attivista (senza @), inserito manualmente dall'organizer. E' usato per matchare i votanti del sondaggio agli attivisti.
- `cube_events/{event_id}` — vedi sezione Firestore sopra.

**Libreria condivisa**: [lib/telegram_client.py](lib/telegram_client.py) — espone:
- Config / encryption: `is_telegram_configured()`, `encrypt_session`, `decrypt_session`, `try_decrypt_session`.
- Wizard programmatic login: `send_code(phone)` → `sign_in_with_code(...)` → opzionale `sign_in_with_password(...)` se 2FA. Tra step lo state vive in `st.session_state` (`tg_step`, `tg_phone`, `tg_phone_code_hash`, `tg_intermediate_session`).
- Stato sessione: `whoami(session)`, `logout(session)`.
- Lettura sondaggi (Fase 2): `parse_telegram_message_link(url) -> (chat_ref, msg_id)`, `get_poll_message(session, chat_ref, msg_id) -> {poll_id, question, options, is_anonymous, is_closed, multiple_choice}`, `get_poll_voters(session, chat_ref, msg_id) -> {options: [{idx, text, voters: [{user_id, username, first_name}], voter_count}], total_voters_unique, ...}`, `resolve_username(session, handle)`.
- Stub Fase 3: `send_dm(session, user_id, text)` (raise NotImplementedError).

**Constraint Telegram da tenere a mente**:
- I sondaggi che vogliamo tracciare devono essere **non-anonimi** (Telegram non espone i votanti dei poll anonimi). `get_poll_voters` solleva `TelegramOperationError` se rileva un poll anonimo.
- La `StringSession` equivale a un login completo Telegram: trattarla come un secret. Cifrata at-rest con Fernet, mai loggata.
- Telethon e' async: tutte le chiamate vanno via `lib.telegram_client._run(coro)` che incapsula `asyncio.run`. Ogni operazione crea/distrugge il proprio `TelegramClient` (no caching tra rerun) — necessario perche' Streamlit fa rerun continui e i client async non sopravvivono fra loop chiusi.
- Il primo login per organizer avviene dal wizard in `pages/1_I_Miei_Attivisti.py`. L'organizer vedra' un nuovo dispositivo "AV Assistant" nelle sue sessioni Telegram; puo' revocarlo in qualunque momento — la prossima `whoami` ritornera' `None` e l'app gli chiedera' di rifare login.
- Il matching votanti -> attivisti avviene per `username` (case-insensitive). Voters senza match vanno in `outside_voters`. Attivisti senza `telegram_username` non sono tracciabili.

**Secrets necessari** (in `.streamlit/secrets.toml` e su Streamlit Cloud):
- `TELEGRAM_API_ID` (int) — da [my.telegram.org/apps](https://my.telegram.org/apps).
- `TELEGRAM_API_HASH` (str) — idem.
- `TELEGRAM_SESSION_FERNET_KEY` — chiave Fernet base64 a 44 caratteri. Generala una volta sola con: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. **Mai cambiarla**: cifra le session degli organizer, ruotarla le invalida tutte.

**Roadmap fasi**:
- Fase 1 (✅ completata): infrastruttura + wizard login + campi Telegram in I Miei Attivisti.
- Fase 2 (✅ completata): pagina "Gestione Cubi" con CRUD eventi + lettura sondaggi via Telethon, dashboard partecipazioni con refresh on-demand.
- Fase 3 (pending): invio DM reminder via Telethon (FloodWait awareness, fallback link `t.me/<username>`).
- Fase 4 (pending): storico partecipazioni aggregato.

## Auth & profili

- Login Google via `st.login("google")` (Authlib, configurato in `.streamlit/secrets.toml`).
- `st.session_state.user_profiles` viene caricato da Firestore al login.
- Esperti con `authorizedProfiles` richiedono login + match profilo.
- Pagine admin/org sono aggiunte alla navigazione in `app.py` solo se il profilo combacia.

## Provider LLM

Tre rami in `pages/chat.py` (cerca `=== LOGICA ...===`): Groq, OpenRouter (API OpenAI-compatibile), Gemini. Le API key sono lette da `st.secrets` (`GROQ_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`); se mancanti, l'utente le inserisce a runtime in sidebar.

## Deploy

Push su GitHub `main` → deploy automatico su Streamlit Cloud: <https://av-assistant-jahevfahcjvxnt7sz5nnwk.streamlit.app/>. Niente CI/CD custom, niente test automatici, niente staging. **Push prudente**: ogni commit su main va in produzione.

I secret (`firebase`, API keys, OAuth Google, Telegram) sono configurati nel pannello Streamlit Cloud, non nel repo. `.streamlit/secrets.toml` locale è gitignored.

## Stato della session_state (chiavi importanti)

- `lang` — `"IT"` / `"EN"`. Inizializzata in `app.py` da query param `?lang=`.
- `user_id` — UUID dal cookie `av_user_id` (anche utenti non loggati hanno persistenza chat).
- `messages` — lista messaggi della chat corrente.
- `current_chat_id` — UUID della chat (cambia su clear / cambio esperto).
- `last_expert_id` / `restored_expert_id` — per gestire reset al cambio esperto vs restore da Firestore.
- `user_profiles`, `logged_in_email` — popolati al login.
- `set_<key>` — valore corrente di ogni `setting` dinamico dell'esperto.
- `tg_step`, `tg_phone`, `tg_phone_code_hash`, `tg_intermediate_session` — wizard di login Telegram (vivono dentro pagina Attivisti tra step).
- `cube_<event_id>_*` — state per-evento nella pagina Gestione Cubi (edit toggle, conferma eliminazione, ecc.).

## Convenzioni

- File config / i18n / prompt **devono** restare data-driven: aggiungere logica hard-coded per un singolo esperto è un anti-pattern qui.
- Le label utente sono **sempre** localizzate (IT/EN). Se aggiungi una stringa UI, aggiungila in `i18n.json` o `ui.json`.
- Le chiavi widget Streamlit dei settings dinamici seguono il pattern `set_<setting_key>` (sono persistite con la chat).
- Quando aggiungi una pagina admin: definiscila in `app.py` e aggiungila alla `pages` list condizionata sul profilo. Non lasciarla rilevare automaticamente da Streamlit.

## Comandi

- Run locale: `streamlit run app.py` (richiede `.streamlit/secrets.toml` popolato).
- Dipendenze: [requirements.txt](requirements.txt) — `streamlit`, `groq`, `google-generativeai`, `openai`, `firebase-admin`, `extra-streamlit-components`, `Authlib`, `telethon`, `cryptography`.

## Cosa NON toccare senza chiedere

- Schema dei documenti Firestore (`users`, `active_chats`, `profiled_chats`, `organizers`, `cube_events`): cambi rompono dati esistenti in produzione.
- Logica cookie / `state_loaded` / `last_expert_id` in `pages/chat.py` (~righe 140–210, 438–451): è delicata, ha bug noti risolti — modifica con test manuali.
- `app.py` — il filtro pagine per profilo è la sola difesa lato UI. Lo schema dei profili admin va mantenuto coerente con Firestore.
- `lib/telegram_client.py` — il pattern "ogni operazione apre/chiude un client" e' deliberato per via dei rerun di Streamlit. Non introdurre caching di `TelegramClient` cross-rerun senza ripensare l'architettura asyncio.
