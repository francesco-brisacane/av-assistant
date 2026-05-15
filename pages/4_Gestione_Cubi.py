"""
Pagina "Gestione Cubi" — riservata a profili org/admin.

Per ogni cubo (evento), l'organizer puo':
  - Crearlo associando un sondaggio Telegram non-anonimo gia' pubblicato nel gruppo del capitolo.
  - Mappare ogni opzione del sondaggio a una categoria: Si' / Forse / No / Ignora.
  - Aggiornare in tempo reale le partecipazioni via Telethon (botton "Aggiorna").
  - Vedere chi ha votato cosa, chi non ha risposto, e i votanti esterni (non nel capitolo).
  - Modificare data/luogo/note, chiudere o riaprire, eliminare l'evento.

Lo storage e' nella collezione Firestore `cube_events`.
"""

import json
import uuid
from datetime import datetime, time

import firebase_admin
import pandas as pd
import streamlit as st
from firebase_admin import firestore

from lib.telegram_client import (
    TelegramConfigError,
    TelegramOperationError,
    get_poll_message,
    get_poll_voters,
    is_telegram_configured,
    parse_telegram_message_link,
    try_decrypt_session,
    whoami as tg_whoami,
)


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


I18N = load_json("data/i18n.json")
lang_code = st.session_state.get("lang", "EN")
current_i18n = I18N.get(lang_code, I18N.get("EN", {}))


def t(key, fallback=""):
    return current_i18n.get(key, fallback or key)


# ---------------------------------------------------------------------------
# Auth & Firebase
# ---------------------------------------------------------------------------

try:
    is_logged_in = st.user.is_logged_in
except AttributeError:
    is_logged_in = st.user.get("email") is not None if hasattr(st.user, "get") else False

if not is_logged_in:
    st.error(t("login_required", "Devi effettuare il login per accedere a questa pagina."))
    st.stop()

user_email = getattr(st.user, "email", None)
user_profiles = st.session_state.get("user_profiles", [])

if "org" not in user_profiles and "admin" not in user_profiles:
    st.error(t("access_denied", "Accesso negato. Questa pagina e' riservata agli organizer."))
    st.stop()

if not firebase_admin._apps:
    st.error(t("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()


# ---------------------------------------------------------------------------
# CSS riusato dalle altre pagine
# ---------------------------------------------------------------------------

st.markdown("""
    <style>
    button[data-testid="stBaseButton-primary"] {
        background-color: #ff4b4b !important;
        color: #fff !important;
        border: 1px solid #ff4b4b !important;
        font-weight: bold !important;
    }
    button[data-testid="stBaseButton-primary"]:hover {
        background-color: #e60000 !important;
        border-color: #e60000 !important;
    }
    div[data-testid="stFormSubmitButton"] button {
        background-color: #28a745 !important;
        color: #fff !important;
        border: 1px solid #28a745 !important;
        font-weight: bold !important;
    }
    div[data-testid="stFormSubmitButton"] button:hover {
        background-color: #218838 !important;
        border-color: #1e7e34 !important;
    }
    </style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Carica dati organizer
# ---------------------------------------------------------------------------

org_ref = db.collection("organizers").document(user_email)
org_doc = org_ref.get()
org_data = org_doc.to_dict() if org_doc.exists else {}
activists = org_data.get("activists", [])
telegram_session_encrypted = org_data.get("telegram_session_encrypted", "")
telegram_chat_id_saved = org_data.get("telegram_chat_id", "")
telegram_chat_title_saved = org_data.get("telegram_chat_title", "")

session_string = try_decrypt_session(telegram_session_encrypted)


# ---------------------------------------------------------------------------
# UI: titolo + banner di stato
# ---------------------------------------------------------------------------

st.title(t("cubes_page_title", "Gestione Cubi"))
st.write(t("cubes_page_desc", "Crea un cubo, collega il sondaggio Telegram e vedi chi ha risposto.") + f" — **{user_email}**")

blocked = False
if not is_telegram_configured():
    st.warning(t("tg_not_configured", "Telegram integration not yet configured by the administrator."))
    blocked = True
elif not session_string:
    st.warning(t("cubes_no_session", "Telegram non connesso. Vai a 'I Miei Attivisti' per collegarlo."))
    blocked = True

if not blocked and not telegram_chat_id_saved:
    st.warning(t("cubes_no_chapter_chat", "Gruppo Telegram del capitolo non configurato. Vai a 'I Miei Attivisti' per impostarlo."))
    blocked = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CATEGORIES = ["ignore", "yes", "maybe", "no"]
CATEGORY_LABELS = {
    "yes": ("✅", "Partecipa"),
    "maybe": ("🤔", "Forse"),
    "no": ("❌", "Non partecipa"),
    "ignore": ("❓", "Non classificata"),
}


def category_label(cat: str, lang: str = "IT") -> str:
    emoji, label_it = CATEGORY_LABELS.get(cat, ("❓", cat))
    return f"{emoji} {label_it}"


def compute_participations(activists_list, options_with_voters):
    """Match votanti -> attivisti per username; ritorna (participations, outside_voters).

    participations: dict keyed by activist email.
    outside_voters: list of dicts per chi ha votato ma non e' nella lista attivisti.
    """
    by_username = {}
    for a in activists_list:
        uname = (a.get("telegram_username") or "").strip().lower().lstrip("@")
        if uname:
            by_username[uname] = a

    participations = {}
    matched_user_ids = set()
    for opt in options_with_voters:
        for v in opt.get("voters", []):
            vuname = (v.get("username") or "").strip().lower()
            if vuname and vuname in by_username:
                act = by_username[vuname]
                # Se vota piu' opzioni (multiple_choice), prendiamo la prima trovata
                if act["email"] not in participations:
                    participations[act["email"]] = {
                        "voted": True,
                        "option_idx": opt["idx"],
                        "option_text": opt["text"],
                        "telegram_user_id": v.get("user_id"),
                    }
                    matched_user_ids.add(v.get("user_id"))

    outside = []
    for opt in options_with_voters:
        for v in opt.get("voters", []):
            if v.get("user_id") in matched_user_ids:
                continue
            outside.append({
                "user_id": v.get("user_id"),
                "username": v.get("username"),
                "first_name": v.get("first_name", ""),
                "last_name": v.get("last_name", ""),
                "option_idx": opt["idx"],
                "option_text": opt["text"],
            })

    return participations, outside


def event_collection():
    return db.collection("cube_events")


def format_event_header(ev):
    data_s = ev.get("data", "")
    ora_s = ev.get("ora", "") or ""
    luogo = ev.get("luogo", "") or "?"
    suffix = ""
    if ev.get("status") == "closed":
        suffix = " (chiuso)"
    when = f"{data_s} {ora_s}".strip()
    return f"📅 {when} — {luogo}{suffix}"


# ---------------------------------------------------------------------------
# Lista eventi
# ---------------------------------------------------------------------------

events_query = (
    event_collection()
    .where("organizer_email", "==", user_email)
    .stream()
)
all_events = []
for e in events_query:
    d = e.to_dict() or {}
    d["_id"] = e.id
    all_events.append(d)

# Ordina: attivi prima per data crescente, chiusi per data decrescente
active_events = sorted(
    [e for e in all_events if e.get("status", "active") != "closed"],
    key=lambda e: (e.get("data", ""), e.get("ora", "")),
)
closed_events = sorted(
    [e for e in all_events if e.get("status", "active") == "closed"],
    key=lambda e: (e.get("data", ""), e.get("ora", "")),
    reverse=True,
)


# ---------------------------------------------------------------------------
# Form: crea nuovo cubo
# ---------------------------------------------------------------------------

def submit_new_cube(form_data, form_ora, form_luogo, form_note, form_poll_link):
    if not form_data or not form_luogo.strip():
        st.error(t("cubes_form_required", "Data e luogo sono obbligatori."))
        return

    poll_data = None
    chat_ref_canonical = None
    msg_id = None
    poll_link_clean = (form_poll_link or "").strip()

    if poll_link_clean:
        try:
            chat_ref, msg_id = parse_telegram_message_link(poll_link_clean)
        except TelegramOperationError as e:
            st.error(str(e))
            return
        try:
            with st.spinner(t("cubes_fetching_poll", "Lettura sondaggio in corso...")):
                poll_data = get_poll_message(session_string, chat_ref, msg_id)
        except TelegramOperationError as e:
            st.error(str(e))
            return
        if poll_data.get("is_anonymous"):
            st.error(t("cubes_poll_anonymous", "Il sondaggio e' anonimo: Telegram non espone chi ha votato. Crea un sondaggio non-anonimo."))
            return
        # chat_ref puo' essere str (username) o int (channel id); serializziamo
        chat_ref_canonical = str(chat_ref)

    new_id = str(uuid.uuid4())
    new_event = {
        "id": new_id,
        "organizer_email": user_email,
        "data": form_data.strftime("%Y-%m-%d"),
        "ora": form_ora.strftime("%H:%M") if form_ora else "",
        "luogo": form_luogo.strip(),
        "note": (form_note or "").strip(),
        "poll_link": poll_link_clean,
        "telegram_chat_ref": chat_ref_canonical or "",
        "telegram_poll_msg_id": msg_id or 0,
        "poll_question": poll_data["question"] if poll_data else "",
        "poll_options": [
            {"idx": o["idx"], "text": o["text"], "category": "ignore"}
            for o in (poll_data["options"] if poll_data else [])
        ],
        "poll_multiple_choice": (poll_data or {}).get("multiple_choice", False),
        "participations": {},
        "outside_voters": [],
        "last_refresh": None,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "status": "active",
    }
    event_collection().document(new_id).set(new_event)
    st.success(t("cubes_created", "Cubo creato con successo!"))
    st.rerun()


if not blocked:
    st.info(t("cubes_vote_required_info", "Per poter leggere i votanti via Telegram, devi prima aver votato tu stesso nel sondaggio (qualunque opzione va bene). E' un vincolo di Telegram, non dell'app."))

with st.expander("➕ " + t("cubes_create_title", "Crea nuovo cubo"), expanded=(len(all_events) == 0 and not blocked)):
    if blocked:
        st.info(t("cubes_complete_setup_first", "Completa la configurazione Telegram prima di creare eventi."))
    else:
        with st.form("create_cube_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                new_data = st.date_input(t("cubes_form_date", "Data"), value=None)
                new_luogo = st.text_input(t("cubes_form_place", "Luogo"), max_chars=100)
            with col2:
                new_ora = st.time_input(t("cubes_form_time", "Ora"), value=time(19, 0))
                new_poll_link = st.text_input(
                    t("cubes_form_poll_link", "Link sondaggio Telegram"),
                    help=t("cubes_form_poll_link_help", "Apri il sondaggio nel gruppo, tocca/clicca i 3 puntini > Copia link, e incolla qui. Deve essere un sondaggio non-anonimo."),
                )
            new_note = st.text_area(t("cubes_form_notes", "Note"), max_chars=500)
            submitted = st.form_submit_button(t("cubes_create_btn", "Crea cubo"))
            if submitted:
                submit_new_cube(new_data, new_ora, new_luogo, new_note, new_poll_link)


# ---------------------------------------------------------------------------
# Render di un singolo evento
# ---------------------------------------------------------------------------

def render_event(ev):
    eid = ev["_id"]
    key_prefix = f"cube_{eid}"

    with st.expander(format_event_header(ev), expanded=False):
        # Info principali
        if ev.get("note"):
            st.caption(ev["note"])

        # Sondaggio info
        if ev.get("poll_link"):
            st.markdown(f"**🗳 {t('cubes_poll', 'Sondaggio')}:** [{ev.get('poll_question') or ev['poll_link']}]({ev['poll_link']})")
        else:
            st.info(t("cubes_no_poll_linked", "Nessun sondaggio collegato. Modifica l'evento per aggiungerlo."))

        # === Sezione 1: mapping opzioni -> categorie ===
        if ev.get("poll_options"):
            with st.container():
                st.markdown(f"**{t('cubes_option_mapping', 'Mappatura opzioni')}**")
                st.caption(t("cubes_option_mapping_desc", "Indica per ogni opzione del sondaggio se equivale a 'Partecipa', 'Forse', 'Non partecipa' o 'Non classificata'."))
                with st.form(f"{key_prefix}_mapping"):
                    new_categories = []
                    cat_options_labels = [
                        ("ignore", t("cubes_cat_ignore", "❓ Non classificata")),
                        ("yes", t("cubes_cat_yes", "✅ Partecipa")),
                        ("maybe", t("cubes_cat_maybe", "🤔 Forse")),
                        ("no", t("cubes_cat_no", "❌ Non partecipa")),
                    ]
                    label_to_cat = {label: cat for cat, label in cat_options_labels}
                    cat_to_label = {cat: label for cat, label in cat_options_labels}
                    label_options = [label for _, label in cat_options_labels]
                    for opt in ev["poll_options"]:
                        cur_cat = opt.get("category", "ignore")
                        cur_label = cat_to_label.get(cur_cat, label_options[0])
                        try:
                            idx_current = label_options.index(cur_label)
                        except ValueError:
                            idx_current = 0
                        choice = st.selectbox(
                            opt["text"],
                            options=label_options,
                            index=idx_current,
                            key=f"{key_prefix}_opt_{opt['idx']}",
                        )
                        new_categories.append({
                            "idx": opt["idx"],
                            "text": opt["text"],
                            "category": label_to_cat[choice],
                        })
                    if st.form_submit_button(t("cubes_save_mapping", "Salva mappatura")):
                        event_collection().document(eid).update({
                            "poll_options": new_categories,
                            "updated_at": firestore.SERVER_TIMESTAMP,
                        })
                        st.success(t("cubes_mapping_saved", "Mappatura salvata."))
                        st.rerun()

        # === Sezione 2: refresh + dashboard ===
        col_r1, col_r2 = st.columns([2, 6])
        with col_r1:
            refresh_clicked = st.button(
                "🔄 " + t("cubes_refresh", "Aggiorna da Telegram"),
                key=f"{key_prefix}_refresh",
                disabled=(ev.get("status") == "closed" or not ev.get("telegram_chat_ref") or not ev.get("telegram_poll_msg_id")),
            )
        with col_r2:
            last = ev.get("last_refresh")
            if last:
                try:
                    if hasattr(last, "strftime"):
                        last_str = last.strftime("%Y-%m-%d %H:%M")
                    else:
                        last_str = str(last)
                except Exception:
                    last_str = "?"
                st.caption(f"{t('cubes_last_refresh', 'Ultimo aggiornamento')}: {last_str}")
            else:
                st.caption(t("cubes_never_refreshed", "Mai aggiornato"))

        if refresh_clicked:
            try:
                with st.spinner(t("cubes_refreshing", "Leggo i votanti dal sondaggio...")):
                    voters_data = get_poll_voters(
                        session_string,
                        ev["telegram_chat_ref"],
                        ev["telegram_poll_msg_id"],
                    )
                # Reload attivisti (lista dinamica)
                current_org = org_ref.get().to_dict() or {}
                current_activists = current_org.get("activists", [])
                participations, outside = compute_participations(current_activists, voters_data["options"])
                event_collection().document(eid).update({
                    "participations": participations,
                    "outside_voters": outside,
                    "last_refresh": firestore.SERVER_TIMESTAMP,
                    "poll_question": voters_data["question"],
                    "updated_at": firestore.SERVER_TIMESTAMP,
                })
                st.success(t("cubes_refresh_done", "Aggiornato!"))
                st.rerun()
            except TelegramOperationError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"{t('cubes_refresh_error', 'Errore refresh')}: {e}")

        # === Sezione 3: dashboard partecipazioni ===
        st.markdown(f"**{t('cubes_dashboard', 'Stato partecipazioni')}**")
        cat_by_idx = {o["idx"]: o.get("category", "ignore") for o in ev.get("poll_options", [])}
        participations = ev.get("participations", {})

        # Lista attivisti correnti (dinamica)
        rows = []
        counters = {"yes": 0, "maybe": 0, "no": 0, "ignore": 0, "no_response": 0, "no_username": 0}
        for a in activists:
            uname = (a.get("telegram_username") or "").strip().lstrip("@")
            email = a.get("email", "")
            nome = a.get("nome", "")
            cognome = a.get("cognome", "")
            if not uname:
                counters["no_username"] += 1
                rows.append({
                    "Nome": nome,
                    "Cognome": cognome,
                    "Telegram": "—",
                    "Stato": "⚠️ " + t("cubes_status_no_username", "No Telegram"),
                    "Opzione": "",
                })
                continue
            part = participations.get(email)
            if not part:
                counters["no_response"] += 1
                rows.append({
                    "Nome": nome,
                    "Cognome": cognome,
                    "Telegram": "@" + uname,
                    "Stato": "⏳ " + t("cubes_status_no_response", "Non risposto"),
                    "Opzione": "",
                })
            else:
                cat = cat_by_idx.get(part.get("option_idx"), "ignore")
                counters[cat] = counters.get(cat, 0) + 1
                rows.append({
                    "Nome": nome,
                    "Cognome": cognome,
                    "Telegram": "@" + uname,
                    "Stato": category_label(cat),
                    "Opzione": part.get("option_text", ""),
                })

        # Riepilogo contatori
        st.write(
            f"✅ {counters['yes']} · 🤔 {counters['maybe']} · ❌ {counters['no']} · ❓ {counters['ignore']} · "
            f"⏳ {counters['no_response']} · ⚠️ {counters['no_username']}"
        )

        if rows:
            df_rows = pd.DataFrame(rows)
            st.dataframe(df_rows, hide_index=True, use_container_width=True)
        else:
            st.info(t("cubes_no_activists_chapter", "Nessun attivista nel capitolo."))

        # === Sezione 4: votanti esterni ===
        outside = ev.get("outside_voters", [])
        if outside:
            with st.expander(f"🚫 {t('cubes_outside_voters', 'Votanti non nel capitolo')} ({len(outside)})"):
                out_rows = []
                for o in outside:
                    name = (o.get("first_name") or "") + (" " + (o.get("last_name") or "") if o.get("last_name") else "")
                    out_rows.append({
                        "Nome": name.strip() or "?",
                        "Telegram": ("@" + o["username"]) if o.get("username") else "—",
                        "Opzione": o.get("option_text", ""),
                    })
                st.dataframe(pd.DataFrame(out_rows), hide_index=True, use_container_width=True)

        # === Sezione 5: azioni ===
        st.markdown("---")
        col_a1, col_a2, col_a3, col_a4 = st.columns([2, 2, 2, 2])
        with col_a1:
            if ev.get("status") == "closed":
                if st.button("🔓 " + t("cubes_reopen", "Riapri"), key=f"{key_prefix}_reopen"):
                    event_collection().document(eid).update({
                        "status": "active",
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    })
                    st.rerun()
            else:
                if st.button("🔒 " + t("cubes_close", "Chiudi"), key=f"{key_prefix}_close"):
                    event_collection().document(eid).update({
                        "status": "closed",
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    })
                    st.rerun()
        with col_a2:
            edit_toggle_key = f"{key_prefix}_edit_open"
            if st.button("✏️ " + t("cubes_edit", "Modifica"), key=f"{key_prefix}_edit"):
                st.session_state[edit_toggle_key] = not st.session_state.get(edit_toggle_key, False)
                st.rerun()
        with col_a3:
            confirm_del_key = f"{key_prefix}_confirm_del"
            if st.button("🗑 " + t("cubes_delete", "Elimina"), key=f"{key_prefix}_delete", type="primary"):
                st.session_state[confirm_del_key] = True
                st.rerun()

        if st.session_state.get(f"{key_prefix}_confirm_del"):
            st.warning(t("cubes_delete_confirm", "Sei sicuro di voler eliminare questo cubo? L'azione e' irreversibile."))
            cd1, cd2, _ = st.columns([2, 2, 6])
            if cd1.button("🗑 " + t("btn_yes_delete", "Si, elimina"), key=f"{key_prefix}_delete_yes", type="primary"):
                event_collection().document(eid).delete()
                st.session_state.pop(f"{key_prefix}_confirm_del", None)
                st.success(t("cubes_deleted", "Cubo eliminato."))
                st.rerun()
            if cd2.button(t("btn_cancel", "Annulla"), key=f"{key_prefix}_delete_no"):
                st.session_state.pop(f"{key_prefix}_confirm_del", None)
                st.rerun()

        if st.session_state.get(f"{key_prefix}_edit_open"):
            st.markdown("---")
            with st.form(f"{key_prefix}_edit_form"):
                try:
                    parsed_date = datetime.strptime(ev.get("data", ""), "%Y-%m-%d").date()
                except Exception:
                    parsed_date = None
                try:
                    h, m = (ev.get("ora", "") or "00:00").split(":")
                    parsed_time = time(int(h), int(m))
                except Exception:
                    parsed_time = time(19, 0)
                c1, c2 = st.columns(2)
                with c1:
                    e_data = st.date_input(t("cubes_form_date", "Data"), value=parsed_date)
                    e_luogo = st.text_input(t("cubes_form_place", "Luogo"), value=ev.get("luogo", ""), max_chars=100)
                with c2:
                    e_ora = st.time_input(t("cubes_form_time", "Ora"), value=parsed_time)
                    e_link = st.text_input(t("cubes_form_poll_link", "Link sondaggio Telegram"), value=ev.get("poll_link", ""))
                e_note = st.text_area(t("cubes_form_notes", "Note"), value=ev.get("note", ""), max_chars=500)

                col_es1, col_es2, _ = st.columns([2, 2, 6])
                save_btn = col_es1.form_submit_button(t("btn_save_changes", "Salva modifiche"), type="primary")
                cancel_btn = col_es2.form_submit_button(t("btn_cancel", "Annulla"))

                if save_btn:
                    update_payload = {
                        "data": e_data.strftime("%Y-%m-%d") if e_data else "",
                        "ora": e_ora.strftime("%H:%M") if e_ora else "",
                        "luogo": e_luogo.strip(),
                        "note": e_note.strip(),
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    }
                    # Se il link e' cambiato, ri-fetch il poll
                    new_link = (e_link or "").strip()
                    if new_link != (ev.get("poll_link") or "").strip():
                        if new_link:
                            try:
                                chat_ref, msg_id = parse_telegram_message_link(new_link)
                                poll_data = get_poll_message(session_string, chat_ref, msg_id)
                                if poll_data.get("is_anonymous"):
                                    st.error(t("cubes_poll_anonymous", "Il sondaggio e' anonimo, non supportato."))
                                    return
                                update_payload.update({
                                    "poll_link": new_link,
                                    "telegram_chat_ref": str(chat_ref),
                                    "telegram_poll_msg_id": msg_id,
                                    "poll_question": poll_data["question"],
                                    "poll_options": [
                                        {"idx": o["idx"], "text": o["text"], "category": "ignore"}
                                        for o in poll_data["options"]
                                    ],
                                    "poll_multiple_choice": poll_data.get("multiple_choice", False),
                                    "participations": {},
                                    "outside_voters": [],
                                    "last_refresh": None,
                                })
                            except TelegramOperationError as exc:
                                st.error(str(exc))
                                return
                        else:
                            # Rimuovi il link
                            update_payload.update({
                                "poll_link": "",
                                "telegram_chat_ref": "",
                                "telegram_poll_msg_id": 0,
                                "poll_question": "",
                                "poll_options": [],
                                "participations": {},
                                "outside_voters": [],
                                "last_refresh": None,
                            })
                    event_collection().document(eid).update(update_payload)
                    st.session_state.pop(f"{key_prefix}_edit_open", None)
                    st.success(t("cubes_updated", "Cubo aggiornato."))
                    st.rerun()
                if cancel_btn:
                    st.session_state.pop(f"{key_prefix}_edit_open", None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Layout: eventi attivi + eventi chiusi
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader(t("cubes_active_title", "Cubi attivi"))
if active_events:
    for ev in active_events:
        render_event(ev)
else:
    st.info(t("cubes_no_active", "Nessun cubo attivo."))

if closed_events:
    with st.expander(t("cubes_closed_title", "Cubi chiusi") + f" ({len(closed_events)})", expanded=False):
        for ev in closed_events:
            render_event(ev)
