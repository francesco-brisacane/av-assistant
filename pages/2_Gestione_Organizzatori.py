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

def apply_delete_button_css():
    st.markdown("""
        <style>
        div.stButton > button[kind="primary"] {
            background-color: #ff4b4b !important;
            color: white !important;
            border: none !important;
        }
        div.stButton > button[kind="primary"]:hover {
            background-color: #ff3333 !important;
            color: white !important;
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

user_profiles = st.session_state.get("user_profiles", [])

if "admin" not in user_profiles:
    st.error(current_i18n.get("admin_required", "Accesso negato. Questa pagina è riservata agli amministratori."))
    st.stop()

# 2. INIZIALIZZAZIONE FIREBASE
if not firebase_admin._apps:
    st.error(current_i18n.get("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()

# 3. UI PRINCIPALE
apply_delete_button_css()
st.title(current_i18n.get("page_title_organizers", "Gestione organizzatori"))
st.write(current_i18n.get("manage_organizers_desc", "Elenco organizzatori registrati nella piattaforma."))

# 4. FETCH DATI E VISUALIZZAZIONE
# Stream user documents that have "org" profile
organizers = []
try:
    users_ref = db.collection("users")
    query = users_ref.where("profiles", "array_contains", "org").stream()
    
    for doc in query:
        data = doc.to_dict()
        email = doc.id
        nome = data.get("nome", "")
        cognome = data.get("cognome", "")
        organizers.append({"nome": nome, "cognome": cognome, "email": email})
except Exception as e:
    st.error(f"Error fetching organizers: {e}")

if organizers:
    df = pd.DataFrame(organizers)
    
    c_nome = current_i18n.get("table_name", "Nome")
    c_cognome = current_i18n.get("table_surname", "Cognome")
    c_email = current_i18n.get("table_email", "Email")
    
    df = df.rename(columns={"nome": c_nome, "cognome": c_cognome, "email": c_email})
    if set([c_nome, c_cognome, c_email]).issubset(df.columns):
        df = df[[c_nome, c_cognome, c_email]]
    st.markdown(f"*{current_i18n.get('select_to_delete', 'Seleziona le righe nella tabella, poi clicca il pulsante di eliminazione.')}*")
    
    event = st.dataframe(
        df, 
        use_container_width=True, 
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row"
    )
    
    # Lista degli indici selezionati
    selected_indices = event.selection.rows
    
    # Pulsante "Elimina N selezionati"
    if len(selected_indices) > 0 and "confirm_delete_orgs" not in st.session_state:
        delete_btn_label = current_i18n.get('delete_selected_btn', 'Elimina {count} Selezionati').replace("{count}", str(len(selected_indices)))
        if st.button(f"🗑️ {delete_btn_label}"):
            st.session_state.confirm_delete_orgs = selected_indices
            st.rerun()
            
    # LOGICA DI CONFERMA ELIMINAZIONE
    if "confirm_delete_orgs" in st.session_state:
        indices_to_delete = st.session_state.confirm_delete_orgs
        
        # Recupera gli oggetti organizzatori
        orgs_to_del = [organizers[i] for i in indices_to_delete]
        count = len(orgs_to_del)
        
        warn_msg = current_i18n.get("delete_selected_confirm", "Sei sicuro di voler eliminare definitivamente {count} organizzatori selezionati?").replace("attivisti", "organizzatori").replace("{count}", str(count))
        st.warning(warn_msg)
        
        col_yes, col_no, _ = st.columns([2, 2, 6])
        if col_yes.button(f"🗑️ {current_i18n.get('btn_yes_delete', 'Sì, Elimina')}", type="primary", key="btn_confirm_del_org", use_container_width=True):
            try:
                batch = db.batch()
                
                for org in orgs_to_del:
                    email_to_del = org["email"]
                    user_ref = db.collection("users").document(email_to_del)
                    user_doc = user_ref.get()
                    
                    if user_doc.exists:
                        u_data = user_doc.to_dict()
                        profiles = u_data.get("profiles", [])
                        
                        if len(profiles) == 1 and profiles[0] == "org":
                            # Ha SOLO il profilo org --> Elimina intero documento
                            batch.delete(user_ref)
                        else:
                            # Ha anche altri profili --> rimuovi solo il profilo org
                            batch.update(user_ref, {"profiles": firestore.ArrayRemove(["org"])})
                            
                batch.commit()
                st.success(current_i18n.get("delete_success", "Organizzatore eliminato con successo!").replace("Attivista", "Organizzatore"))
                del st.session_state.confirm_delete_orgs
                st.rerun()
                
            except Exception as e:
                st.error(f"{current_i18n.get('save_error', 'Errore:')} {e}")
                
        if col_no.button(f"❌ {current_i18n.get('btn_cancel', 'Annulla')}", key="btn_cancel_del_org", use_container_width=True):
            del st.session_state.confirm_delete_orgs
            st.rerun()
else:
    st.info(current_i18n.get("no_organizers", "Nessun organizzatore trovato."))

st.markdown("---")

# 5. FORM INSERIMENTO
st.subheader(current_i18n.get("add_organizer", "Aggiungi Organizzatore"))

with st.form("add_org_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        new_nome = st.text_input(current_i18n.get("form_name", "Nome"), max_chars=50)
    with col2:
        new_cognome = st.text_input(current_i18n.get("form_surname", "Cognome"), max_chars=50)
    
    new_email = st.text_input(current_i18n.get("form_email", "Email (Account Google)"), max_chars=100)
    
    submitted = st.form_submit_button(current_i18n.get("form_add_button", "Aggiungi"))
    
    if submitted:
        if not new_nome or not new_cognome or not new_email:
            st.error(current_i18n.get("form_mandatory_fields", "Tutti i campi sono obbligatori."))
        else:
            new_email = new_email.strip().lower()
            
            # 6. TRANSAZIONE/UPDATE FIREBASE
            try:
                user_ref = db.collection("users").document(new_email)
                user_doc = user_ref.get()
                
                if user_doc.exists:
                    # Se l'utente esiste, aggiorniamo solo i profili per non sovrascrivere nome o cognome esistenti
                    user_ref.update({"profiles": firestore.ArrayUnion(["org"])})
                else:
                    # Se l'utente non c'è, lo creiamo da zero con questi dati
                    user_data = {
                        "nome": new_nome.strip(),
                        "cognome": new_cognome.strip(),
                        "profiles": ["org"]
                    }
                    user_ref.set(user_data)
                
                st.success(current_i18n.get("organizer_added_success", "Organizzatore aggiunto/aggiornato con successo!"))
                st.rerun() 
                
            except Exception as e:
                err_save = current_i18n.get("save_error", "Si è verificato un errore durante il salvataggio:")
                st.error(f"{err_save} {e}")
