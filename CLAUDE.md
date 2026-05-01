# CLAUDE.md

Guida per Claude Code per lavorare su questo progetto. Il progetto è di **Anonymous for the Voiceless (AV)**: un multi-chatbot Streamlit configurabile, multilingua (IT/EN), con auth Google e backend Firebase.

## Architettura in breve

- **Entry point**: [app.py](app.py) — definisce le `st.Page` e la navigazione condizionata dai profili utente. Le pagine in [pages/](pages/) **non** sono auto-discoverate: la sidebar nativa di Streamlit è nascosta via CSS (`stSidebarNav { display: none }`) e l'unica navigazione visibile è quella costruita in `app.py`.
- **Chat principale**: [pages/chat.py](pages/chat.py) — un unico file che gestisce caricamento config, cookie utente, login Google, selezione esperto, rendering settings, costruzione system prompt, dispatch del provider LLM (Groq / Gemini / OpenRouter), streaming risposta, persistenza Firestore. Quando si aggiunge una feature alla chat, **quasi sempre si tocca questo file**.
- **Pagine admin/org** ([pages/1_I_Miei_Attivisti.py](pages/1_I_Miei_Attivisti.py), [pages/2_Gestione_Organizzatori.py](pages/2_Gestione_Organizzatori.py), [pages/3_Chat_Attivisti.py](pages/3_Chat_Attivisti.py)) — gestione utenti / capitoli / visualizzazione chat profilate. Visibili solo a profili `org` / `admin` / `activist` (vedi `app.py` per la logica).

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
- `users/{email}` — `{ profiles: [...], nome, cognome, ... }`. Profili noti: `admin`, `org`, `activist`.
- `active_chats/{user_id}` — chat live per cookie `av_user_id` (UUID per utente, anche anonimo).
- `profiled_chats/{chat_id}` — log permanente (solo per esperti con `authorizedProfiles` non vuoto + utente loggato).

## Auth & profili

- Login Google via `st.login("google")` (Authlib, configurato in `.streamlit/secrets.toml`).
- `st.session_state.user_profiles` viene caricato da Firestore al login.
- Esperti con `authorizedProfiles` richiedono login + match profilo.
- Pagine admin/org sono aggiunte alla navigazione in `app.py` solo se il profilo combacia.

## Provider LLM

Tre rami in `pages/chat.py` (cerca `=== LOGICA ...===`): Groq, OpenRouter (API OpenAI-compatibile), Gemini. Le API key sono lette da `st.secrets` (`GROQ_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`); se mancanti, l'utente le inserisce a runtime in sidebar.

## Deploy

Push su GitHub `main` → deploy automatico su Streamlit Cloud: <https://av-assistant-jahevfahcjvxnt7sz5nnwk.streamlit.app/>. Niente CI/CD custom, niente test automatici, niente staging. **Push prudente**: ogni commit su main va in produzione.

I secret (`firebase`, API keys, OAuth Google) sono configurati nel pannello Streamlit Cloud, non nel repo. `.streamlit/secrets.toml` locale è gitignored.

## Stato della session_state (chiavi importanti)

- `lang` — `"IT"` / `"EN"`. Inizializzata in `app.py` da query param `?lang=`.
- `user_id` — UUID dal cookie `av_user_id` (anche utenti non loggati hanno persistenza chat).
- `messages` — lista messaggi della chat corrente.
- `current_chat_id` — UUID della chat (cambia su clear / cambio esperto).
- `last_expert_id` / `restored_expert_id` — per gestire reset al cambio esperto vs restore da Firestore.
- `user_profiles`, `logged_in_email` — popolati al login.
- `set_<key>` — valore corrente di ogni `setting` dinamico dell'esperto.

## Convenzioni

- File config / i18n / prompt **devono** restare data-driven: aggiungere logica hard-coded per un singolo esperto è un anti-pattern qui.
- Le label utente sono **sempre** localizzate (IT/EN). Se aggiungi una stringa UI, aggiungila in `i18n.json` o `ui.json`.
- Le chiavi widget Streamlit dei settings dinamici seguono il pattern `set_<setting_key>` (sono persistite con la chat).
- Quando aggiungi una pagina admin: definiscila in `app.py` e aggiungila alla `pages` list condizionata sul profilo. Non lasciarla rilevare automaticamente da Streamlit.

## Comandi

- Run locale: `streamlit run app.py` (richiede `.streamlit/secrets.toml` popolato).
- Dipendenze: [requirements.txt](requirements.txt) — `streamlit`, `groq`, `google-generativeai`, `openai`, `firebase-admin`, `extra-streamlit-components`, `Authlib`.

## Cosa NON toccare senza chiedere

- Schema dei documenti Firestore (`users`, `active_chats`, `profiled_chats`): cambi rompono dati esistenti in produzione.
- Logica cookie / `state_loaded` / `last_expert_id` in `pages/chat.py` (~righe 140–210, 438–451): è delicata, ha bug noti risolti — modifica con test manuali.
- `app.py` — il filtro pagine per profilo è la sola difesa lato UI. Lo schema dei profili admin va mantenuto coerente con Firestore.
