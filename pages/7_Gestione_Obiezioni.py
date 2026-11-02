"""
Pagina "Gestione Obiezioni" — riservata org/admin.

  - Richieste: coda delle obiezioni proposte dagli attivisti. Per ognuna l'org puo'
    rispondere con una nuova scheda (form precompilato), collegarla a una scheda esistente
    (doppione) o rifiutarla con una nota. L'attivista vede l'esito in "Le mie richieste".
  - Catalogo: CRUD delle schede per lingua. Gli org archiviano/ripristinano, solo l'admin
    elimina definitivamente. L'admin puo' importare i contenuti iniziali da
    data/objections_seed.json (idempotente).

Quando si apre l'editor di una scheda la pagina mostra solo il form (i tab di Streamlit
non si possono selezionare da codice, cosi' l'editor resta sempre in primo piano).
"""

import json
import uuid

import firebase_admin
import streamlit as st
from firebase_admin import firestore

from lib.objections import (
    CATEGORIES,
    SEED_FILE,
    category_label,
    delete_objection,
    import_seed,
    load_objections,
    load_open_requests_count,
    load_requests,
    render_objection_body,
    resolve_request,
    save_objection,
    search_objections,
    set_objection_status,
    split_lines,
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
is_admin = "admin" in user_profiles

if "org" not in user_profiles and not is_admin:
    st.error(t("access_denied", "Accesso negato. Questa pagina e' riservata agli organizer."))
    st.stop()

if not firebase_admin._apps:
    st.error(t("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()

LANGS = ["IT", "EN"]


def flash(msg):
    st.session_state["objm_flash"] = msg


def open_editor(objection=None, request=None):
    """Apre l'editor: scheda esistente, nuova vuota o nuova da una richiesta."""
    if objection:
        prefill = dict(objection)
    else:
        prefill = {"lang": (request or {}).get("lang", lang_code), "category": "other",
                   "question": (request or {}).get("text", ""), "status": "published"}
    st.session_state["objm_edit"] = {
        "id": objection["id"] if objection else None,
        "request_id": request["id"] if request else None,
        "request": request,
        "prefill": prefill,
        "nonce": uuid.uuid4().hex[:8],
    }


# ---------------------------------------------------------------------------
# Editor scheda (sostituisce l'intera pagina quando attivo)
# ---------------------------------------------------------------------------

edit = st.session_state.get("objm_edit")
if edit:
    p = edit["prefill"]
    k = f"objm_f_{edit['nonce']}"

    if edit["id"]:
        st.title("✏️ " + t("objm_edit_title", "Modifica scheda"))
    else:
        st.title("➕ " + t("objm_new_title", "Nuova scheda"))

    req = edit.get("request")
    if req:
        with st.container(border=True):
            st.markdown("**📬 " + t("objm_answering_request", "Stai rispondendo alla richiesta di") + f" {req.get('requested_by_name') or req.get('requested_by')}**")
            st.markdown(f"🗨️ _{req.get('text', '')}_")
            if req.get("context"):
                st.caption(t("obj_context", "Contesto") + ": " + req["context"])

    with st.form(f"{k}_form"):
        c1, c2 = st.columns(2)
        with c1:
            f_lang = st.selectbox(t("objm_field_lang", "Lingua"), LANGS,
                                  index=LANGS.index(p.get("lang", lang_code)) if p.get("lang") in LANGS else 0,
                                  key=f"{k}_lang")
        with c2:
            f_cat = st.selectbox(t("obj_filter_category", "Categoria"), CATEGORIES,
                                 index=CATEGORIES.index(p["category"]) if p.get("category") in CATEGORIES else len(CATEGORIES) - 1,
                                 format_func=lambda c: category_label(c, t), key=f"{k}_cat")
        f_question = st.text_input(t("objm_field_question", "Obiezione") + " *", value=p.get("question", ""), key=f"{k}_q")
        f_variants = st.text_area(t("objm_field_variants", "Varianti (una per riga)"),
                                  value="\n".join(p.get("variants") or []), height=100, key=f"{k}_var",
                                  help=t("objm_field_variants_help", "Altri modi in cui viene detta: migliorano la ricerca."))
        f_short = st.text_area(t("objm_field_short", "Risposta breve") + " *", value=p.get("short_answer", ""), height=90, key=f"{k}_short",
                               help=t("objm_field_short_help", "1-2 frasi da ricordare a memoria."))
        f_socratic = st.text_area(t("objm_field_socratic", "Domande socratiche (una per riga)"),
                                  value="\n".join(p.get("socratic_questions") or []), height=120, key=f"{k}_soc")
        f_body = st.text_area(t("objm_field_body", "Approfondimento (markdown)"), value=p.get("body_md", ""), height=220, key=f"{k}_body")
        f_pitfalls = st.text_area(t("objm_field_pitfalls", "Errori da evitare"), value=p.get("pitfalls", ""), height=90, key=f"{k}_pit")
        f_sources = st.text_area(t("objm_field_sources", "Fonti / link (una per riga)"),
                                 value="\n".join(p.get("sources") or []), height=80, key=f"{k}_src")

        b1, b2 = st.columns(2)
        submitted = b1.form_submit_button("💾 " + t("objm_btn_save", "Salva"), use_container_width=True)
        cancelled = b2.form_submit_button(t("objm_btn_cancel", "Annulla"), use_container_width=True)

    if cancelled:
        del st.session_state["objm_edit"]
        st.rerun()

    if submitted:
        if not f_question.strip() or not f_short.strip():
            st.error(t("objm_required_fields", "Obiezione e risposta breve sono obbligatorie."))
        else:
            data = {
                "lang": f_lang,
                "category": f_cat,
                "question": f_question,
                "variants": split_lines(f_variants),
                "short_answer": f_short,
                "socratic_questions": split_lines(f_socratic),
                "body_md": f_body,
                "pitfalls": f_pitfalls,
                "sources": split_lines(f_sources),
                "status": p.get("status", "published"),
                "source_request_id": edit.get("request_id"),
            }
            try:
                obj_id = save_objection(db, data, user_email, objection_id=edit["id"])
                if edit.get("request_id"):
                    resolve_request(db, edit["request_id"], "answered", user_email, objection_id=obj_id)
                    flash(t("objm_request_answered", "Scheda pubblicata e richiesta evasa."))
                else:
                    flash(t("objm_saved", "Scheda salvata."))
                del st.session_state["objm_edit"]
                st.rerun()
            except Exception as e:
                st.error(t("obj_error_save", "Errore durante il salvataggio") + f": {e}")
    st.stop()


# ---------------------------------------------------------------------------
# UI principale
# ---------------------------------------------------------------------------

st.title("🛡️ " + t("objm_page_title", "Gestione Obiezioni"))
st.write(t("objm_page_desc", "Cura il catalogo condiviso delle obiezioni e rispondi alle richieste degli attivisti."))

msg = st.session_state.pop("objm_flash", None)
if msg:
    st.success(msg)

open_count = load_open_requests_count(db)
tab_requests, tab_catalog = st.tabs([
    "📬 " + t("objm_tab_requests", "Richieste") + (f" ({open_count})" if open_count else ""),
    "📚 " + t("obj_tab_catalog", "Catalogo"),
])

all_published = load_objections(db)


def format_objection_option(obj_id):
    o = next((x for x in all_published if x["id"] == obj_id), None)
    return f"[{o.get('lang')}] {o.get('question', '')}" if o else obj_id


# --- Richieste --------------------------------------------------------------
with tab_requests:
    status_options = ["open", "answered", "merged", "rejected", "__all__"]
    status_labels = {
        "open": "⏳ " + t("obj_req_status_open", "In attesa di risposta"),
        "answered": "✅ " + t("obj_req_status_answered", "Risposta pubblicata"),
        "merged": "🔗 " + t("obj_req_status_merged", "Collegata a una scheda esistente"),
        "rejected": "🚫 " + t("obj_req_status_rejected", "Non accolta"),
        "__all__": t("objm_status_all", "Tutte"),
    }
    status_filter = st.selectbox(t("objm_filter_status", "Stato"), status_options,
                                 format_func=lambda s: status_labels[s], key="objm_req_status")
    requests = load_requests(db, status=None if status_filter == "__all__" else status_filter)

    if not requests:
        st.info(t("objm_no_requests", "Nessuna richiesta in questo stato."))

    for req in requests:
        rid = req["id"]
        with st.container(border=True):
            st.markdown(f"**🗨️ {req.get('text', '')}**")
            author = req.get("requested_by_name") or req.get("requested_by", "")
            if req.get("requested_by_name"):
                author += f" ({req.get('requested_by')})"
            st.caption(f"[{req.get('lang', '')}] · {author} · {(req.get('created_at') or '')[:10]} · {status_labels.get(req.get('status'), req.get('status'))}")
            if req.get("context"):
                st.markdown("_" + t("obj_context", "Contesto") + ":_ " + req["context"])

            if req.get("status") != "open":
                if req.get("objection_id"):
                    st.caption("📖 " + format_objection_option(req["objection_id"]))
                if req.get("resolver_note"):
                    st.caption("📝 " + req["resolver_note"])
                if req.get("resolved_by"):
                    st.caption(t("objm_resolved_by", "Gestita da") + f" {req['resolved_by']}")
                continue

            similar = search_objections(req.get("text", ""), all_published, min_score=0.45, limit=3)
            if similar:
                st.markdown(t("objm_similar_existing", "Schede simili già presenti:"))
                for o in similar:
                    st.markdown(f"- [{o.get('lang')}] {o.get('question', '')}")

            a1, a2, a3 = st.columns(3)
            with a1:
                if st.button("✍️ " + t("objm_btn_answer", "Rispondi con nuova scheda"), key=f"objm_ans_{rid}", use_container_width=True):
                    open_editor(request=req)
                    st.rerun()
            with a2:
                with st.popover("🔗 " + t("objm_btn_merge", "Collega a esistente"), use_container_width=True):
                    if not all_published:
                        st.info(t("obj_catalog_empty", "Il catalogo è ancora vuoto in questa lingua."))
                    else:
                        ordered = similar + [o for o in all_published if o not in similar]
                        target = st.selectbox(t("objm_merge_target", "Scheda"), [o["id"] for o in ordered],
                                              format_func=format_objection_option, key=f"objm_merge_sel_{rid}")
                        note = st.text_input(t("objm_note_optional", "Nota per l'attivista (facoltativa)"), key=f"objm_merge_note_{rid}")
                        if st.button(t("objm_btn_confirm", "Conferma"), key=f"objm_merge_ok_{rid}"):
                            resolve_request(db, rid, "merged", user_email, objection_id=target, note=note)
                            flash(t("objm_request_merged", "Richiesta collegata alla scheda esistente."))
                            st.rerun()
            with a3:
                with st.popover("🚫 " + t("objm_btn_reject", "Rifiuta"), use_container_width=True):
                    note = st.text_input(t("objm_reject_note", "Motivo (visibile all'attivista)"), key=f"objm_rej_note_{rid}")
                    if st.button(t("objm_btn_confirm", "Conferma"), key=f"objm_rej_ok_{rid}", disabled=not note.strip()):
                        resolve_request(db, rid, "rejected", user_email, note=note)
                        flash(t("objm_request_rejected", "Richiesta rifiutata."))
                        st.rerun()


# --- Catalogo ---------------------------------------------------------------
with tab_catalog:
    col_l, col_q, col_btn = st.columns([2, 6, 3])
    with col_l:
        cat_lang = st.selectbox(t("objm_field_lang", "Lingua"), LANGS, index=LANGS.index(lang_code), key="objm_cat_lang")
    with col_q:
        cat_query = st.text_input(t("obj_search_label", "Cerca un'obiezione"), key="objm_cat_search")
    with col_btn:
        st.write("")
        if st.button("➕ " + t("objm_btn_new", "Nuova scheda"), use_container_width=True):
            open_editor()
            st.session_state["objm_edit"]["prefill"]["lang"] = cat_lang
            st.rerun()

    show_archived = st.toggle(t("objm_show_archived", "Mostra archiviate"), key="objm_show_archived")
    catalog = load_objections(db, lang=cat_lang, include_archived=show_archived)
    results = search_objections(cat_query, catalog)
    st.caption(t("obj_results_count", "{n} schede").format(n=len(results)))

    if not catalog:
        st.info(t("obj_catalog_empty", "Il catalogo è ancora vuoto in questa lingua."))

    for obj in results:
        oid = obj["id"]
        archived = obj.get("status") == "archived"
        title = ("🗄️ " if archived else "🗨️ ") + obj.get("question", "")
        with st.expander(title):
            render_objection_body(obj, t)
            st.caption(t("objm_last_edit", "Ultima modifica") + f": {obj.get('updated_by', '')} · {(obj.get('updated_at') or '')[:10]}")

            b1, b2, b3 = st.columns(3)
            if b1.button("✏️ " + t("objm_btn_edit", "Modifica"), key=f"objm_edit_{oid}", use_container_width=True):
                open_editor(objection=obj)
                st.rerun()
            if archived:
                if b2.button("♻️ " + t("objm_btn_restore", "Ripristina"), key=f"objm_restore_{oid}", use_container_width=True):
                    set_objection_status(db, oid, "published", user_email)
                    st.rerun()
            elif b2.button("🗄️ " + t("objm_btn_archive", "Archivia"), key=f"objm_archive_{oid}", use_container_width=True):
                set_objection_status(db, oid, "archived", user_email)
                st.rerun()
            if is_admin:
                confirm_key = f"objm_confirm_del_{oid}"
                if st.session_state.get(confirm_key):
                    st.warning(t("objm_delete_confirm", "Eliminare definitivamente questa scheda?"))
                    c_yes, c_no = st.columns(2)
                    if c_yes.button(t("objm_btn_confirm", "Conferma"), key=f"objm_del_yes_{oid}"):
                        delete_objection(db, oid)
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                    if c_no.button(t("objm_btn_cancel", "Annulla"), key=f"objm_del_no_{oid}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                elif b3.button("🗑️ " + t("objm_btn_delete", "Elimina"), key=f"objm_del_{oid}", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.rerun()

    if is_admin:
        st.markdown("---")
        with st.expander("📦 " + t("objm_seed_title", "Contenuti iniziali")):
            try:
                with open(SEED_FILE, "r", encoding="utf-8") as f:
                    seed_count = len(json.load(f))
            except FileNotFoundError:
                seed_count = 0
            st.write(t("objm_seed_desc", "Importa le {n} schede di partenza (IT/EN). Le schede già importate non vengono toccate, quindi puoi rilanciarlo senza rischi.").format(n=seed_count))
            if st.button("📥 " + t("objm_seed_btn", "Importa contenuti iniziali"), disabled=seed_count == 0):
                imported, skipped = import_seed(db, user_email)
                flash(t("objm_seed_done", "Importate {imported} schede ({skipped} già presenti).").format(imported=imported, skipped=skipped))
                st.rerun()
