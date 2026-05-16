import streamlit as st
import firebase_admin
from firebase_admin import firestore
import pandas as pd
import json

import time as _time
from lib.telegram_client import (
    TelegramConfigError,
    TelegramLoginError,
    TelegramOperationError,
    encrypt_session,
    is_telegram_configured,
    logout as tg_logout,
    normalize_phone_to_e164,
    resolve_phone as tg_resolve_phone,
    send_code as tg_send_code,
    sign_in_with_code as tg_sign_in_with_code,
    sign_in_with_password as tg_sign_in_with_password,
    try_decrypt_session,
    whoami as tg_whoami,
)

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

I18N = load_json("data/i18n.json")
lang_code = st.session_state.get("lang", "EN")
current_i18n = I18N.get(lang_code, I18N["EN"])

def apply_custom_css():
    st.markdown("""
        <style>
        /* GLOBAL PRIMARY - Rosso per azioni distruttive (es. Elimina) */
        button[data-testid="stBaseButton-primary"] {
            background-color: #ff4b4b !important;
            color: #ffffff !important;
            border: 1px solid #ff4b4b !important;
            font-weight: bold !important;
        }
        button[data-testid="stBaseButton-primary"]:hover {
            background-color: #e60000 !important;
            border-color: #e60000 !important;
        }

        /* FORM SUBMIT - Verde per azioni positive (es. Salva, Aggiungi) */
        div[data-testid="stFormSubmitButton"] button {
            background-color: #28a745 !important;
            color: #ffffff !important;
            border: 1px solid #28a745 !important;
            font-weight: bold !important;
            width: 100% !important;
        }
        div[data-testid="stFormSubmitButton"] button:hover {
            background-color: #218838 !important;
            border-color: #1e7e34 !important;
        }
        
        /* BOTTONI SECONDARI - Bordi più definiti per visibilità */
        button[data-testid="stBaseButton-secondary"] {
            border: 1px solid #999999 !important;
            color: #31333f !important;
            font-weight: 500 !important;
        }

        /* Correzione colore testo per tutti i bottoni primari */
        button[data-testid="stBaseButton-primary"] p, 
        div[data-testid="stFormSubmitButton"] button p {
            color: #ffffff !important;
        }
        </style>
    """, unsafe_allow_html=True)

# 1. AUTENTICAZIONE E AUTORIZZAZIONE
try:
    is_logged_in = st.user.is_logged_in
except AttributeError:
    is_logged_in = st.user.get("email") is not None if hasattr(st.user, "get") else False

if not is_logged_in:
    st.error(current_i18n.get("login_required", "Devi effettuare il login per accedere a questa pagina."))
    st.stop()

user_email = getattr(st.user, 'email', None)
user_profiles = st.session_state.get("user_profiles", [])

if "org" not in user_profiles and "admin" not in user_profiles:
    st.error(current_i18n.get("access_denied", "Accesso negato. Questa pagina è riservata agli organizer."))
    st.stop()

# 2. INIZIALIZZAZIONE FIREBASE
if not firebase_admin._apps:
    st.error(current_i18n.get("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()
COLLECTION_NAME = "organizers"

# 3. UI PRINCIPALE
apply_custom_css()
st.title(current_i18n.get("page_title", "I miei attivisti"))
st.write(f"{current_i18n.get('manage_activists', 'Gestione attivisti per:')} **{user_email}**")

# 4. FETCH DATI E VISUALIZZAZIONE
doc_ref = db.collection(COLLECTION_NAME).document(user_email)
doc = doc_ref.get()

if doc.exists:
    data = doc.to_dict()
    activists = data.get("activists", [])
    telegram_session_encrypted = data.get("telegram_session_encrypted", "")
    telegram_chat_id_saved = data.get("telegram_chat_id", "")
    telegram_chat_title_saved = data.get("telegram_chat_title", "")
else:
    activists = []
    telegram_session_encrypted = ""
    telegram_chat_id_saved = ""
    telegram_chat_title_saved = ""
    # Crea il documento vuoto se non esiste
    doc_ref.set({"activists": []})

# 4.5 SEZIONE INTEGRAZIONE TELEGRAM
st.markdown("---")
with st.expander(f"📡 {current_i18n.get('tg_section_title', 'Connect your Telegram')}", expanded=False):
    st.write(current_i18n.get("tg_section_desc", ""))

    if not is_telegram_configured():
        st.warning(current_i18n.get("tg_not_configured", "Telegram integration not yet configured by the administrator."))
    else:
        # Stato attuale: prova a decifrare la session salvata e verifica
        existing_session = try_decrypt_session(telegram_session_encrypted)
        me = None
        if existing_session:
            with st.spinner("..."):
                me = tg_whoami(existing_session)

        if me:
            # Caso: gia' connesso
            who_str = ("@" + me["username"]) if me.get("username") else (me.get("first_name") or str(me.get("id")))
            st.success(current_i18n.get("tg_status_connected", "Connected as {who}").replace("{who}", who_str))
            col_d1, _ = st.columns([2, 6])
            if col_d1.button(f"🔌 {current_i18n.get('tg_btn_disconnect', 'Disconnect Telegram')}", key="tg_btn_disconnect"):
                with st.spinner("..."):
                    tg_logout(existing_session)
                doc_ref.set({"telegram_session_encrypted": ""}, merge=True)
                for k in ("tg_step", "tg_phone", "tg_phone_code_hash", "tg_intermediate_session"):
                    st.session_state.pop(k, None)
                st.success(current_i18n.get("tg_disconnect_success", "Telegram disconnected."))
                st.rerun()
        else:
            # Caso: non connesso, oppure session non piu' valida
            if telegram_session_encrypted and existing_session is None:
                st.warning(current_i18n.get("tg_session_invalid", "Saved Telegram session no longer valid. Please reconnect."))
            else:
                st.info(current_i18n.get("tg_status_disconnected", "Telegram not connected."))

            st.caption(current_i18n.get("tg_privacy_warning", ""))

            step = st.session_state.get("tg_step")

            if step in (None, "phone"):
                phone_input = st.text_input(
                    current_i18n.get("tg_phone_label", "Phone number"),
                    key="tg_phone_input",
                    placeholder="+39...",
                )
                if st.button(f"📨 {current_i18n.get('tg_btn_send_code', 'Send code')}", key="tg_btn_send_code"):
                    if not phone_input.strip():
                        st.error(current_i18n.get("form_mandatory_fields", "All fields are mandatory."))
                    else:
                        try:
                            with st.spinner("..."):
                                inter_session, code_hash = tg_send_code(phone_input.strip())
                            st.session_state.tg_phone = phone_input.strip()
                            st.session_state.tg_phone_code_hash = code_hash
                            st.session_state.tg_intermediate_session = inter_session
                            st.session_state.tg_step = "code"
                            st.rerun()
                        except TelegramLoginError as e:
                            st.error(current_i18n.get("tg_generic_error", "Telegram error: {error}").replace("{error}", str(e)))
                        except TelegramConfigError as e:
                            st.error(str(e))

            elif step == "code":
                st.info(current_i18n.get("tg_code_sent", "Code sent via Telegram."))
                code_input = st.text_input(
                    current_i18n.get("tg_code_label", "Code received"),
                    key="tg_code_input",
                    max_chars=10,
                )
                col_c1, col_c2, _ = st.columns([2, 2, 6])
                if col_c1.button(f"✅ {current_i18n.get('tg_btn_verify_code', 'Verify code')}", key="tg_btn_verify_code"):
                    try:
                        with st.spinner("..."):
                            next_session, needs_2fa = tg_sign_in_with_code(
                                st.session_state.tg_intermediate_session,
                                st.session_state.tg_phone,
                                code_input.strip(),
                                st.session_state.tg_phone_code_hash,
                            )
                        if needs_2fa:
                            st.session_state.tg_intermediate_session = next_session
                            st.session_state.tg_step = "password"
                            st.rerun()
                        else:
                            enc = encrypt_session(next_session)
                            doc_ref.set({"telegram_session_encrypted": enc}, merge=True)
                            for k in ("tg_step", "tg_phone", "tg_phone_code_hash", "tg_intermediate_session"):
                                st.session_state.pop(k, None)
                            st.success(current_i18n.get("tg_login_success", "Telegram connected successfully!"))
                            st.rerun()
                    except TelegramLoginError as e:
                        st.error(current_i18n.get("tg_generic_error", "Telegram error: {error}").replace("{error}", str(e)))
                if col_c2.button(f"❌ {current_i18n.get('tg_btn_cancel_login', 'Cancel')}", key="tg_btn_cancel_code"):
                    for k in ("tg_step", "tg_phone", "tg_phone_code_hash", "tg_intermediate_session"):
                        st.session_state.pop(k, None)
                    st.rerun()

            elif step == "password":
                st.info(current_i18n.get("tg_2fa_prompt", "Enter your 2FA password."))
                pwd_input = st.text_input(
                    current_i18n.get("tg_password_label", "2FA password"),
                    key="tg_password_input",
                    type="password",
                )
                col_p1, col_p2, _ = st.columns([2, 2, 6])
                if col_p1.button(f"✅ {current_i18n.get('tg_btn_verify_password', 'Verify password')}", key="tg_btn_verify_password"):
                    try:
                        with st.spinner("..."):
                            final_session = tg_sign_in_with_password(
                                st.session_state.tg_intermediate_session,
                                pwd_input,
                            )
                        enc = encrypt_session(final_session)
                        doc_ref.set({"telegram_session_encrypted": enc}, merge=True)
                        for k in ("tg_step", "tg_phone", "tg_phone_code_hash", "tg_intermediate_session"):
                            st.session_state.pop(k, None)
                        st.success(current_i18n.get("tg_login_success", "Telegram connected successfully!"))
                        st.rerun()
                    except TelegramLoginError as e:
                        st.error(current_i18n.get("tg_generic_error", "Telegram error: {error}").replace("{error}", str(e)))
                if col_p2.button(f"❌ {current_i18n.get('tg_btn_cancel_login', 'Cancel')}", key="tg_btn_cancel_password"):
                    for k in ("tg_step", "tg_phone", "tg_phone_code_hash", "tg_intermediate_session"):
                        st.session_state.pop(k, None)
                    st.rerun()

# 4.6 SEZIONE GRUPPO TELEGRAM DEL CAPITOLO
with st.expander(f"📍 {current_i18n.get('chapter_section_title', 'Chapter Telegram group')}", expanded=False):
    st.write(current_i18n.get("chapter_section_desc", ""))
    with st.form("chapter_form", clear_on_submit=False):
        chat_id_input = st.text_input(
            current_i18n.get("chapter_chat_id_label", "Telegram group chat ID"),
            value=telegram_chat_id_saved,
            help=current_i18n.get("chapter_chat_id_help", ""),
        )
        chat_title_input = st.text_input(
            current_i18n.get("chapter_chat_title_label", "Group name"),
            value=telegram_chat_title_saved,
        )
        chapter_submitted = st.form_submit_button(
            current_i18n.get("chapter_btn_save", "Save group configuration")
        )
        if chapter_submitted:
            try:
                doc_ref.set({
                    "telegram_chat_id": chat_id_input.strip(),
                    "telegram_chat_title": chat_title_input.strip(),
                }, merge=True)
                st.success(current_i18n.get("chapter_saved_success", "Chapter group configuration saved."))
                st.rerun()
            except Exception as e:
                st.error(f"{current_i18n.get('save_error', 'Save error:')} {e}")

# 4.7 SEZIONE SINCRONIZZAZIONE TELEGRAM DA NUMERI
existing_session_for_sync = try_decrypt_session(telegram_session_encrypted)
with st.expander(f"🔄 {current_i18n.get('phone_sync_title', 'Sincronizza Telegram da numeri')}", expanded=False):
    st.write(current_i18n.get("phone_sync_desc", "Per ogni attivista con numero di telefono ma senza username Telegram, prova a risolvere via il numero. Utile per chi non ha un @username pubblico."))
    if not is_telegram_configured():
        st.warning(current_i18n.get("tg_not_configured", "Telegram integration not yet configured."))
    elif not existing_session_for_sync:
        st.warning(current_i18n.get("cubes_no_session", "Telegram non connesso."))
    else:
        # Conta candidati (attivisti senza user_id che hanno un telefono)
        candidates = []
        unresolvable_phones = []
        for a in activists:
            if a.get("telegram_user_id"):
                continue  # gia' risolto
            tel = a.get("telefono", "")
            if not tel:
                continue  # niente numero, niente sync
            normalized = normalize_phone_to_e164(tel)
            if not normalized:
                unresolvable_phones.append({
                    "nome": a.get("nome", ""),
                    "cognome": a.get("cognome", ""),
                    "telefono": tel,
                })
                continue
            candidates.append(a)

        n_total = len(candidates)
        if n_total == 0:
            st.info(current_i18n.get("phone_sync_nothing_to_do", "Nessun attivista da risolvere (tutti gia' hanno user_id Telegram o numero non normalizzabile)."))
        else:
            st.write(current_i18n.get("phone_sync_will_process", "Saranno processati {n} attivisti.").replace("{n}", str(n_total)))
            if st.button(f"🔄 {current_i18n.get('phone_sync_btn', 'Avvia sincronizzazione')}", key="btn_phone_sync", type="primary"):
                progress = st.empty()
                results_rows = []
                for i, act in enumerate(candidates, start=1):
                    progress.info(
                        current_i18n.get("phone_sync_progress", "Sincronizzo {n}/{total} — {name}")
                        .replace("{n}", str(i))
                        .replace("{total}", str(n_total))
                        .replace("{name}", f"{act.get('nome', '')} {act.get('cognome', '')}")
                    )
                    try:
                        res = tg_resolve_phone(existing_session_for_sync, act.get("telefono", ""))
                    except TelegramOperationError as exc:
                        progress.error(str(exc))
                        results_rows.append({
                            "Nome": f"{act.get('nome', '')} {act.get('cognome', '')}",
                            "Tel": act.get("telefono", ""),
                            "Esito": f"❌ {exc}",
                        })
                        break
                    if res.get("ok"):
                        # Aggiorna l'attivista (ArrayRemove vecchio + ArrayUnion nuovo)
                        updated = dict(act)
                        updated["telegram_user_id"] = res["user_id"]
                        if res.get("username") and not updated.get("telegram_username"):
                            updated["telegram_username"] = res["username"]
                        try:
                            batch = db.batch()
                            batch.update(doc_ref, {"activists": firestore.ArrayRemove([act])})
                            batch.update(doc_ref, {"activists": firestore.ArrayUnion([updated])})
                            batch.commit()
                            results_rows.append({
                                "Nome": f"{act.get('nome', '')} {act.get('cognome', '')}",
                                "Tel": res.get("phone_e164") or act.get("telefono", ""),
                                "Esito": f"✅ user_id={res['user_id']}" + (f", @{res['username']}" if res.get('username') else " (senza username)"),
                            })
                        except Exception as exc:
                            results_rows.append({
                                "Nome": f"{act.get('nome', '')} {act.get('cognome', '')}",
                                "Tel": res.get("phone_e164") or act.get("telefono", ""),
                                "Esito": f"⚠️ Salvataggio fallito: {exc}",
                            })
                    else:
                        err_code = res.get("error", "unknown")
                        err_label = {
                            "normalize_failed": "❌ Numero non normalizzabile",
                            "not_on_telegram_or_privacy": "❌ Non su Telegram o privacy",
                            "session_invalid": "🔌 Sessione non valida",
                        }.get(err_code, f"❌ {err_code}")
                        results_rows.append({
                            "Nome": f"{act.get('nome', '')} {act.get('cognome', '')}",
                            "Tel": res.get("phone_e164") or act.get("telefono", ""),
                            "Esito": err_label,
                        })
                    _time.sleep(1.5)  # anti-FloodWait

                progress.empty()
                ok_count = sum(1 for r in results_rows if r["Esito"].startswith("✅"))
                fail_count = len(results_rows) - ok_count
                st.success(
                    current_i18n.get("phone_sync_done", "Sincronizzazione completata: {ok} OK, {fail} falliti.")
                    .replace("{ok}", str(ok_count))
                    .replace("{fail}", str(fail_count))
                )
                if results_rows:
                    st.dataframe(pd.DataFrame(results_rows), hide_index=True, use_container_width=True)

        if unresolvable_phones:
            with st.expander(f"⚠️ {current_i18n.get('phone_sync_unnormalizable', 'Numeri non normalizzabili')} ({len(unresolvable_phones)})"):
                for u in unresolvable_phones:
                    st.write(f"- {u['nome']} {u['cognome']}: `{u['telefono']}`")
                st.caption(current_i18n.get("phone_sync_unnormalizable_hint", "Modifica l'attivista e correggi il numero in formato +39... o 320XXXXXXX."))

st.markdown("---")

if activists:
    df = pd.DataFrame(activists)
    
    c_nome = current_i18n.get("table_name", "Nome")
    c_cognome = current_i18n.get("table_surname", "Cognome")
    c_email = current_i18n.get("table_email", "Email")
    c_data_ingresso = current_i18n.get("table_entry_date", "Data Ingresso")
    c_telefono = current_i18n.get("table_phone", "Telefono")
    c_provincia = current_i18n.get("table_province", "Provincia")
    c_note = current_i18n.get("table_notes", "Note")
    c_telegram = current_i18n.get("table_telegram", "Telegram")

    # Assicurati che le colonne esistano nel DF, altrimenti metti stringa vuota
    for col in ["data_ingresso", "telefono", "provincia", "note", "telegram_username"]:
        if col not in df.columns:
            df[col] = ""

    # Converte data_ingresso in vero datetime per il DateColumn (Streamlit)
    if "data_ingresso" in df.columns:
        df["data_ingresso"] = pd.to_datetime(df["data_ingresso"], errors='coerce')

    df = df.rename(columns={
        "nome": c_nome,
        "cognome": c_cognome,
        "email": c_email,
        "data_ingresso": c_data_ingresso,
        "telefono": c_telefono,
        "provincia": c_provincia,
        "note": c_note,
        "telegram_username": c_telegram
    })

    cols_to_show = [c_nome, c_cognome, c_email, c_telegram, c_data_ingresso, c_telefono, c_provincia, c_note]
    existing_cols_to_show = [c for c in cols_to_show if c in df.columns]
    df = df[existing_cols_to_show]
        
    st.markdown(f"*{current_i18n.get('select_to_delete', 'Seleziona le righe nella tabella, poi clicca il pulsante di eliminazione.')}*")
        
    # Tabella principale con selezione multipla (Streamlit >= 1.35)
    event = st.dataframe(
        df, 
        use_container_width=True, 
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            c_email: st.column_config.TextColumn(width="medium"),
            c_telegram: st.column_config.TextColumn(width="small"),
            c_data_ingresso: st.column_config.DateColumn(format="DD/MM/YYYY"),
            c_note: st.column_config.TextColumn(width="large", help="Note sull'attivista")
        }
    )
    
    # Lista degli indici selezionati
    selected_indices = event.selection.rows
    
    # Mostra pulsante "Elimina N selezionati" solo se ci sono selezioni e NON siamo già in fase di conferma
    if len(selected_indices) > 0 and "confirm_delete" not in st.session_state and "editing_activist" not in st.session_state:
        col_btns1, col_btns2 = st.columns([2, 8])
        
        with col_btns1:
            delete_btn_label = current_i18n.get('delete_selected_btn', 'Elimina {count} Selezionati').replace("{count}", str(len(selected_indices)))
            if st.button(f"🗑️ {delete_btn_label}"):
                st.session_state.confirm_delete = selected_indices
                st.rerun()
        
        with col_btns2:
            if len(selected_indices) == 1:
                if st.button(f"📝 {current_i18n.get('edit_activist_btn', 'Modifica attivista selezionato')}"):
                    st.session_state.editing_activist = activists[selected_indices[0]]
                    st.rerun()
            
    # LOGICA DI MODIFICA
    if "editing_activist" in st.session_state:
        st.markdown("---")
        st.subheader(current_i18n.get("update_activist_title", "Modifica Attivista"))
        act = st.session_state.editing_activist
        
        with st.form("edit_activist_form"):
            col1, col2 = st.columns(2)
            
            # Gestione data per il picker
            try:
                current_date = pd.to_datetime(act.get("data_ingresso", "")).date()
            except:
                current_date = None
                
            with col1:
                edit_nome = st.text_input(current_i18n.get("form_name", "Nome"), value=act.get("nome", ""), max_chars=50)
                edit_data_ingresso = st.date_input(current_i18n.get("form_entry_date", "Data Ingresso in AV"), value=current_date)
                edit_provincia = st.text_input(current_i18n.get("form_province", "Provincia"), value=act.get("provincia", ""), max_chars=50)
            with col2:
                edit_cognome = st.text_input(current_i18n.get("form_surname", "Cognome"), value=act.get("cognome", ""), max_chars=50)
                edit_telefono = st.text_input(current_i18n.get("form_phone", "Telefono"), value=act.get("telefono", ""), max_chars=20)
                edit_email = st.text_input(current_i18n.get("form_email", "Email (Google Account)"), value=act.get("email", ""), max_chars=100, disabled=True)

            edit_telegram = st.text_input(
                current_i18n.get("form_telegram", "Telegram username (senza @)"),
                value=act.get("telegram_username", ""),
                max_chars=50,
                help=current_i18n.get("form_telegram_help", ""),
            )
            edit_note = st.text_area(current_i18n.get("form_notes", "Note"), value=act.get("note", ""))
            
            c1, c2, _ = st.columns([2, 2, 6])
            save_submitted = c1.form_submit_button(current_i18n.get("btn_save_changes", "Salva Modifiche"), type="primary")
            cancel_edit = c2.form_submit_button(current_i18n.get("btn_cancel", "Annulla"))
            
            if save_submitted:
                updated_act = {
                    "nome": edit_nome.strip(),
                    "cognome": edit_cognome.strip(),
                    "email": edit_email.strip().lower(),
                    "data_ingresso": edit_data_ingresso.strftime("%Y-%m-%d") if edit_data_ingresso else "",
                    "telefono": edit_telefono.strip(),
                    "provincia": edit_provincia.strip(),
                    "note": edit_note.strip(),
                    "telegram_username": edit_telegram.strip().lstrip("@"),
                }
                
                try:
                    # Per aggiornare un elemento in un array firestore: ArrayRemove vecchio, ArrayUnion nuovo
                    batch = db.batch()
                    batch.update(doc_ref, {"activists": firestore.ArrayRemove([act])})
                    batch.update(doc_ref, {"activists": firestore.ArrayUnion([updated_act])})
                    
                    # Aggiorna anche la collection users (solo nome e cognome)
                    user_ref = db.collection("users").document(edit_email.strip().lower())
                    batch.update(user_ref, {
                        "nome": edit_nome.strip(),
                        "cognome": edit_cognome.strip()
                    })
                    
                    batch.commit()
                    st.success(current_i18n.get("activist_added_success", "Aggiornato con successo!"))
                    del st.session_state.editing_activist
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore: {e}")
            
            if cancel_edit:
                del st.session_state.editing_activist
                st.rerun()
            
    # LOGICA DI CONFERMA ELIMINAZIONE
    if "confirm_delete" in st.session_state:
        indices_to_delete = st.session_state.confirm_delete
        
        # Recupera gli oggetti attivista completi in base agli indici salvati
        acts_to_del = [activists[i] for i in indices_to_delete]
        count = len(acts_to_del)
        
        warn_msg = current_i18n.get("delete_selected_confirm", "Sei sicuro di voler eliminare definitivamente {count} attivisti selezionati?").replace("{count}", str(count))
        st.warning(warn_msg)
        
        # Colonne più larghe
        col_yes, col_no, _ = st.columns([2, 2, 6])
        if col_yes.button(f"🗑️ {current_i18n.get('btn_yes_delete', 'Sì, Elimina')}", type="primary", key="btn_confirm_del", use_container_width=True):
            try:
                # Esegui una transazione batch Firestore per sicurezza e velocità
                batch = db.batch()
                
                # 1. Rimuovi dall'organizer corrente (ArrayRemove accetta array multipli)
                batch.update(doc_ref, {
                    "activists": firestore.ArrayRemove(acts_to_del)
                })
                
                # Precarichiamo altri organizer una sola volta
                other_orgs_query = list(db.collection(COLLECTION_NAME).stream())
                
                # Elimina ogni attivista individualmente
                for act_to_del in acts_to_del:
                    email_to_del = act_to_del["email"]
                    exists_elsewhere = False
                    
                    # 2. Controlla in altri organizer
                    for org_doc in other_orgs_query:
                        if org_doc.id != user_email: 
                            org_data = org_doc.to_dict()
                            if any(a.get("email") == email_to_del for a in org_data.get("activists", [])):
                                exists_elsewhere = True
                                break
                    
                    # 3. Aggiorna users 
                    if not exists_elsewhere:
                        user_ref = db.collection("users").document(email_to_del)
                        user_doc = user_ref.get()
                        if user_doc.exists:
                            u_data = user_doc.to_dict()
                            profiles = u_data.get("profiles", [])
                            
                            if len(profiles) == 1 and profiles[0] == "activist":
                                batch.delete(user_ref)
                            else:
                                batch.update(user_ref, {"profiles": firestore.ArrayRemove(["activist"])})
                
                batch.commit()
                
                st.success(current_i18n.get("delete_success", "Attivista eliminato con successo!"))
                del st.session_state.confirm_delete
                st.rerun()
                
            except Exception as e:
                st.error(f"{current_i18n.get('save_error', 'Errore:')} {e}")
                
        if col_no.button(f"❌ {current_i18n.get('btn_cancel', 'Annulla')}", key="btn_cancel_del", use_container_width=True):
            del st.session_state.confirm_delete
            st.rerun()

else:
    st.info(current_i18n.get("no_activists", "Nessun attivista presente nel tuo capitolo."))

st.markdown("---")

# 5. FORM INSERIMENTO
st.subheader(current_i18n.get("add_activist", "Aggiungi Attivista"))

with st.form("add_activist_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        new_nome = st.text_input(current_i18n.get("form_name", "Nome"), max_chars=50)
        new_data_ingresso = st.date_input(current_i18n.get("form_entry_date", "Data Ingresso in AV"), value=None)
        new_provincia = st.text_input(current_i18n.get("form_province", "Provincia"), max_chars=50)
    with col2:
        new_cognome = st.text_input(current_i18n.get("form_surname", "Cognome"), max_chars=50)
        new_telefono = st.text_input(current_i18n.get("form_phone", "Telefono"), max_chars=20)
        new_email = st.text_input(current_i18n.get("form_email", "Email (Google Account)"), max_chars=100)

    new_telegram = st.text_input(
        current_i18n.get("form_telegram", "Telegram username (senza @)"),
        max_chars=50,
        help=current_i18n.get("form_telegram_help", ""),
    )
    new_note = st.text_area(current_i18n.get("form_notes", "Note"))
    
    submitted = st.form_submit_button(current_i18n.get("form_add_button", "Aggiungi"))
    
    if submitted:
        if not new_nome or not new_cognome or not new_email:
            st.error(current_i18n.get("form_mandatory_fields", "Tutti i campi sono obbligatori."))
        else:
            new_email = new_email.strip().lower()
            
            # Controllo duplicati base email
            email_exists = any(a.get("email") == new_email for a in activists)
            
            if email_exists:
                err_msg = current_i18n.get("activist_already_exists", "L'attivista con email {email} è già presente nella tua lista.")
                st.error(err_msg.replace("{email}", new_email))
            else:
                # 6. TRANSAZIONE FIREBASE
                new_activist_data = {
                    "nome": new_nome.strip(),
                    "cognome": new_cognome.strip(),
                    "email": new_email,
                    "data_ingresso": new_data_ingresso.strftime("%Y-%m-%d") if new_data_ingresso else "",
                    "telefono": new_telefono.strip(),
                    "provincia": new_provincia.strip(),
                    "note": new_note.strip(),
                    "telegram_username": new_telegram.strip().lstrip("@"),
                }
                
                try:
                    batch = db.batch()
                    
                    # A. Aggiungi all'array dell'organizer
                    batch.update(doc_ref, {"activists": firestore.ArrayUnion([new_activist_data])})
                    
                    # B. Aggiorna o crea l'utente nella collection users aggiungendo profilo "activist"
                    user_ref = db.collection("users").document(new_email)
                    user_doc = user_ref.get()
                    
                    if user_doc.exists:
                        # Se esiste, aggiorniamo solo i profili senza sovrascrivere nome/cognome esistenti
                        batch.update(user_ref, {"profiles": firestore.ArrayUnion(["activist"])})
                    else:
                        # Se non esiste, creiamo l'utente con tutti i suoi dati di base
                        batch.set(user_ref, {
                            "nome": new_nome.strip(),
                            "cognome": new_cognome.strip(),
                            "profiles": ["activist"]
                        })
                    
                    batch.commit()
                    
                    st.success(current_i18n.get("activist_added_success", "Attivista aggiunto con successo!"))
                    st.rerun() # Ricarica per mostrare la tabella aggiornata
                    
                except Exception as e:
                    err_save = current_i18n.get("save_error", "Si è verificato un errore durante il salvataggio:")
                    st.error(f"{err_save} {e}")
