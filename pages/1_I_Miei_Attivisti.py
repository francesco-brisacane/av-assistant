import streamlit as st
import firebase_admin
from firebase_admin import firestore
import pandas as pd
import json

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

I18N = load_json("data/i18n.json")
lang_code = st.session_state.get("lang", "EN")
current_i18n = I18N.get(lang_code, I18N["EN"])

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
st.title(current_i18n.get("page_title", "I miei attivisti"))
st.write(f"{current_i18n.get('manage_activists', 'Gestione attivisti per:')} **{user_email}**")

# 4. FETCH DATI E VISUALIZZAZIONE
doc_ref = db.collection(COLLECTION_NAME).document(user_email)
doc = doc_ref.get()

if doc.exists:
    data = doc.to_dict()
    activists = data.get("activists", [])
else:
    activists = []
    # Crea il documento vuoto se non esiste
    doc_ref.set({"activists": []})

if activists:
    df = pd.DataFrame(activists)
    
    c_nome = current_i18n.get("table_name", "Nome")
    c_cognome = current_i18n.get("table_surname", "Cognome")
    c_email = current_i18n.get("table_email", "Email")
    
    df = df.rename(columns={"nome": c_nome, "cognome": c_cognome, "email": c_email})
    if set([c_nome, c_cognome, c_email]).issubset(df.columns):
        df = df[[c_nome, c_cognome, c_email]]
        
    st.markdown(f"*{current_i18n.get('select_to_delete', 'Seleziona le righe nella tabella, poi clicca il pulsante di eliminazione.')}*")
        
    # Tabella principale con selezione multipla (Streamlit >= 1.35)
    event = st.dataframe(
        df, 
        use_container_width=True, 
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row"
    )
    
    # Lista degli indici selezionati
    selected_indices = event.selection.rows
    
    # Mostra pulsante "Elimina N selezionati" solo se ci sono selezioni e NON siamo già in fase di conferma
    if len(selected_indices) > 0 and "confirm_delete" not in st.session_state:
        delete_btn_label = current_i18n.get('delete_selected_btn', 'Elimina {count} Selezionati').replace("{count}", str(len(selected_indices)))
        if st.button(f"🗑️ {delete_btn_label}"):
            st.session_state.confirm_delete = selected_indices
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
    with col2:
        new_cognome = st.text_input(current_i18n.get("form_surname", "Cognome"), max_chars=50)
    
    new_email = st.text_input(current_i18n.get("form_email", "Email (Google Account)"), max_chars=100)
    
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
                    "email": new_email
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
