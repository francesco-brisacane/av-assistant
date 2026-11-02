"""
Pagina "Obiezioni" — per attivisti, org e admin.

  - Catalogo: ricerca (tollerante a typo/accenti) + filtro categoria sulle schede pubblicate
    nella lingua corrente.
  - Allenati: flashcard con un'obiezione a caso, prima pensi alla risposta poi la scopri.
  - Proponi: l'attivista chiede una risposta condivisa per un'obiezione non censita;
    prima dell'invio vengono mostrate le schede simili per evitare doppioni.
  - Le mie richieste: stato delle proprie richieste, con la scheda di risposta quando pronta.

Le richieste vengono evase dagli org in pages/7_Gestione_Obiezioni.py.
"""

import json
import random

import firebase_admin
import streamlit as st
from firebase_admin import firestore

from lib.objections import (
    CATEGORIES,
    category_label,
    create_request,
    load_objections,
    load_requests,
    normalize,
    render_objection_body,
    search_objections,
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
if lang_code not in ["IT", "EN"]:
    lang_code = "EN"
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

if not any(p in user_profiles for p in ("activist", "org", "admin")):
    st.error(t("access_denied", "Accesso negato."))
    st.stop()

if not firebase_admin._apps:
    st.error(t("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()


def get_user_display_name():
    if "obj_user_name" not in st.session_state:
        name = ""
        try:
            doc = db.collection("users").document(user_email).get()
            if doc.exists:
                d = doc.to_dict() or {}
                name = f"{d.get('nome', '')} {d.get('cognome', '')}".strip()
        except Exception:
            pass
        st.session_state["obj_user_name"] = name
    return st.session_state["obj_user_name"]


STATUS_BADGES = {
    "open": "⏳ " + t("obj_req_status_open", "In attesa di risposta"),
    "answered": "✅ " + t("obj_req_status_answered", "Risposta pubblicata"),
    "merged": "🔗 " + t("obj_req_status_merged", "Collegata a una scheda esistente"),
    "rejected": "🚫 " + t("obj_req_status_rejected", "Non accolta"),
}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🛡️ " + t("obj_page_title", "Obiezioni"))
st.write(t("obj_page_desc", "Studia come rispondere alle obiezioni che sentiamo più spesso in strada."))

objections = load_objections(db, lang=lang_code)
objections_by_id = {o["id"]: o for o in objections}
my_requests = load_requests(db, requested_by=user_email)

tab_catalog, tab_train, tab_propose, tab_mine = st.tabs([
    "📚 " + t("obj_tab_catalog", "Catalogo"),
    "🎲 " + t("obj_tab_train", "Allenati"),
    "✍️ " + t("obj_tab_propose", "Proponi"),
    # Label senza contatori: se cambiano, Streamlit ricrea i tab e torna al primo.
    "📬 " + t("obj_tab_my_requests", "Le mie richieste"),
])

category_options = ["__all__"] + [c for c in CATEGORIES if any(o.get("category") == c for o in objections)]


def format_category(key):
    return t("obj_cat_all", "Tutte le categorie") if key == "__all__" else category_label(key, t)


# --- Catalogo ---------------------------------------------------------------
with tab_catalog:
    if not objections:
        st.info(t("obj_catalog_empty", "Il catalogo è ancora vuoto in questa lingua."))
    else:
        col_q, col_c = st.columns([7, 3])
        with col_q:
            query = st.text_input(
                t("obj_search_label", "Cerca un'obiezione"),
                placeholder=t("obj_search_placeholder", "es. leoni, proteine, piante, tradizione..."),
                key="obj_search",
            )
        with col_c:
            category = st.selectbox(
                t("obj_filter_category", "Categoria"),
                options=category_options,
                format_func=format_category,
                key="obj_filter_cat",
            )

        pool = objections if category == "__all__" else [o for o in objections if o.get("category") == category]
        results = search_objections(query, pool)

        st.caption(t("obj_results_count", "{n} schede").format(n=len(results)))
        if not results:
            st.info(t("obj_no_results", "Nessuna scheda trovata. Se è un'obiezione che senti spesso, proponila nel tab Proponi."))
        for obj in results:
            with st.expander(f"🗨️ {obj.get('question', '')}"):
                render_objection_body(obj, t)


# --- Allenati ---------------------------------------------------------------
with tab_train:
    if not objections:
        st.info(t("obj_catalog_empty", "Il catalogo è ancora vuoto in questa lingua."))
    else:
        st.write(t("obj_train_desc", "Leggi l'obiezione, pensa a come risponderesti, poi confronta con la risposta condivisa."))
        train_cat = st.selectbox(
            t("obj_filter_category", "Categoria"),
            options=category_options,
            format_func=format_category,
            key="obj_train_cat",
        )
        train_pool = objections if train_cat == "__all__" else [o for o in objections if o.get("category") == train_cat]

        current_id = st.session_state.get("obj_train_id")
        if current_id not in {o["id"] for o in train_pool}:
            current_id = None

        if st.button("🎲 " + t("obj_train_next", "Nuova obiezione"), type="primary", key="obj_train_next_btn") or current_id is None:
            candidates = [o for o in train_pool if o["id"] != current_id] or train_pool
            current_id = random.choice(candidates)["id"]
            st.session_state["obj_train_id"] = current_id
            st.session_state["obj_train_reveal"] = False

        card = objections_by_id[current_id]
        with st.container(border=True):
            st.markdown(f"### 🗨️ “{card.get('question', '')}”")
            st.caption("🏷️ " + category_label(card.get("category", "other"), t))

        if st.session_state.get("obj_train_reveal"):
            render_objection_body(card, t)
        elif st.button("👀 " + t("obj_train_reveal", "Mostra la risposta"), key="obj_train_reveal_btn"):
            st.session_state["obj_train_reveal"] = True
            st.rerun()


# --- Proponi ----------------------------------------------------------------
with tab_propose:
    st.write(t("obj_propose_desc", "Hai sentito un'obiezione che non trovi nel catalogo? Proponila: gli organizer prepareranno una risposta condivisa."))

    # Reset dei campi dopo un invio riuscito (va fatto prima di creare i widget).
    if st.session_state.pop("obj_req_reset", False):
        st.session_state["obj_req_text"] = ""
        st.session_state["obj_req_context"] = ""
    if st.session_state.pop("obj_req_sent", False):
        st.success(t("obj_propose_sent", "Richiesta inviata! La trovi nel tab Le mie richieste."))

    req_text = st.text_area(
        t("obj_propose_text", "Obiezione"),
        placeholder=t("obj_propose_text_placeholder", "Scrivila come te l'hanno detta, es. \"Ma gli animali allevati non esisterebbero nemmeno\""),
        key="obj_req_text",
        height=90,
    )
    req_context = st.text_area(
        t("obj_propose_context", "Contesto (facoltativo)"),
        placeholder=t("obj_propose_context_placeholder", "Chi te l'ha detta, come hai risposto, cosa non ha funzionato..."),
        key="obj_req_context",
        height=90,
    )

    similar = search_objections(req_text, objections, min_score=0.5, limit=3) if req_text.strip() else []
    if similar:
        st.markdown("**" + t("obj_propose_similar", "Forse cercavi una di queste? Controlla prima di inviare:") + "**")
        for obj in similar:
            with st.expander(f"🗨️ {obj.get('question', '')}"):
                render_objection_body(obj, t)

    if st.button("📨 " + t("obj_propose_submit", "Invia richiesta"), type="primary", disabled=not req_text.strip()):
        already_open = any(
            r.get("status") == "open" and normalize(r.get("text", "")) == normalize(req_text)
            for r in my_requests
        )
        if already_open:
            st.warning(t("obj_propose_duplicate", "Hai già una richiesta aperta con questo testo."))
        else:
            try:
                create_request(db, req_text, req_context, lang_code, user_email, get_user_display_name())
                st.session_state["obj_req_reset"] = True
                st.session_state["obj_req_sent"] = True
                st.rerun()
            except Exception as e:
                st.error(t("obj_error_save", "Errore durante il salvataggio") + f": {e}")


# --- Le mie richieste -------------------------------------------------------
with tab_mine:
    if not my_requests:
        st.info(t("obj_my_requests_empty", "Non hai ancora inviato richieste."))
    else:
        # Le schede collegate possono essere in un'altra lingua o archiviate.
        linked_ids = {r.get("objection_id") for r in my_requests if r.get("objection_id")}
        linked = {o["id"]: o for o in load_objections(db, include_archived=True) if o["id"] in linked_ids} if linked_ids else {}

        for req in my_requests:
            with st.container(border=True):
                st.markdown(f"**🗨️ {req.get('text', '')}**")
                st.caption(f"{STATUS_BADGES.get(req.get('status'), req.get('status'))} · {(req.get('created_at') or '')[:10]}")
                if req.get("context"):
                    st.caption(t("obj_context", "Contesto") + ": " + req["context"])
                if req.get("resolver_note"):
                    st.markdown("📝 " + t("obj_resolver_note", "Nota dell'organizer") + ": " + req["resolver_note"])
                obj = linked.get(req.get("objection_id"))
                if obj:
                    with st.expander("📖 " + t("obj_view_answer", "Vedi la risposta") + f": {obj.get('question', '')}"):
                        render_objection_body(obj, t)
