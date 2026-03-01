import streamlit as st
import firebase_admin
from firebase_admin import firestore
import pandas as pd
import json
import datetime

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
        
def apply_custom_chat_css():
    st.markdown("""
        <style>
        /* STILE MESSAGGI PER IL VIEWER */
        [data-testid="stChatMessage"]:nth-child(odd) { 
            background-color: #111111; 
            border: 1px solid #333; 
        }
        [data-testid="stChatMessage"]:nth-child(even) { 
            background-color: #000000; 
            border: 1px solid #555; 
        }
        </style>
        """, unsafe_allow_html=True)

I18N = load_json("data/i18n.json")
EXPERTS_CONFIG = load_json("data/experts.json")

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

if "org" not in user_profiles and "activist" not in user_profiles:
    st.error(current_i18n.get("access_denied", "Accesso negato. Questa pagina è riservata agli organizer e agli attivisti."))
    st.stop()

# 2. INIZIALIZZAZIONE FIREBASE E DB
if not firebase_admin._apps:
    st.error(current_i18n.get("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()
ORGANIZERS_COLLECTION = "organizers"
PROFILED_CHATS_COLLECTION = "profiled_chats"

# 3. UI PRINCIPALE
if "org" in user_profiles:
    st.title(current_i18n.get("page_title_chats", "Chat dei tuoi Attivisti"))
    st.write(current_i18n.get("chat_viewer_desc", "Revisiona le conversazioni gestite dagli attivisti del tuo capitolo."))
else:
    st.title(current_i18n.get("page_title_chats_activist", "Le tue Chat"))
    
st.markdown("---")

apply_custom_chat_css()

# 4. FETCH DATI E UI FILTRI
activist_emails = []
activist_map = {} # mappa email -> Nome Cognome
search_emails = []

if "org" in user_profiles:
    # --- LOGICA PER ORGANIZER ---
    doc_ref = db.collection(ORGANIZERS_COLLECTION).document(user_email)
    doc = doc_ref.get()

    if doc.exists:
        data = doc.to_dict()
        activists = data.get("activists", [])
        for act in activists:
            email = act.get("email", "")
            if email:
                activist_emails.append(email)
                name_str = f"{act.get('nome', '')} {act.get('cognome', '')}".strip()
                activist_map[email] = name_str if name_str else email

    # Aggiungiamo anche l'organizzatore stesso per fargli vedere le proprie chat
    try:
        user_doc = db.collection("users").document(user_email).get()
        lbl_you = current_i18n.get("you", "Tu")
        if user_doc.exists:
            u_data = user_doc.to_dict()
            org_name = f"{u_data.get('nome', '')} {u_data.get('cognome', '')}".strip()
            activist_emails.append(user_email)
            activist_map[user_email] = f"{org_name} ({lbl_you})" if org_name else f"{user_email} ({lbl_you})"
        else:
            activist_emails.append(user_email)
            activist_map[user_email] = f"{user_email} ({lbl_you})"
    except Exception as e:
        lbl_you = current_i18n.get("you", "Tu")
        activist_emails.append(user_email)
        activist_map[user_email] = f"{user_email} ({lbl_you})"

    if not activist_emails:
        st.info(current_i18n.get("no_chats_found", "Nessuna chat trovata per i tuoi attivisti."))
        st.stop()

    # Opzione di base "Tutti" per la combobox
    lbl_all = current_i18n.get("filter_all_activists", "Tutti gli attivisti")
    filter_options = [lbl_all] + list(activist_map.values())
    filter_email_map = {lbl_all: "ALL"}
    for e, n in activist_map.items():
        filter_email_map[n] = e

    lbl_filter = current_i18n.get("filter_label", "Filtra per Attivista")
    selected_filter = st.selectbox(lbl_filter, filter_options)
    selected_email = filter_email_map[selected_filter]
    
    # Riduciamo search_emails a quello scelto
    search_emails = activist_emails if selected_email == "ALL" else [selected_email]

else:
    # --- LOGICA PER SEMPLICE ATTIVISTA ---
    # Non fetchiamo i child, isoliamo strettamente il suo account
    search_emails = [user_email]
    
    try:
        user_doc = db.collection("users").document(user_email).get()
        lbl_you = current_i18n.get("you", "Tu")
        if user_doc.exists:
            u_data = user_doc.to_dict()
            act_name = f"{u_data.get('nome', '')} {u_data.get('cognome', '')}".strip()
            activist_map[user_email] = f"{act_name} ({lbl_you})" if act_name else f"{user_email} ({lbl_you})"
        else:
            activist_map[user_email] = f"{user_email} ({lbl_you})"
    except Exception as e:
        lbl_you = current_i18n.get("you", "Tu")
        activist_map[user_email] = f"{user_email} ({lbl_you})"

# 5. FETCH CHATS
all_chats = []

# Funzione per smezzare l'array in chunk di 10
def chunk_array(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

try:
    for chunk in chunk_array(search_emails, 10):
        # Filtro sulle email scelte dal dropdown
        query = db.collection(PROFILED_CHATS_COLLECTION).where("email", "in", chunk).stream()
        for chat_doc in query:
            chat_data = chat_doc.to_dict()
            all_chats.append(chat_data)
except Exception as e:
    st.error(f"Error fetching chats: {e}")

if not all_chats:
    st.info(current_i18n.get("no_chats_found", "Nessuna chat trovata per i tuoi attivisti."))
    st.stop()

# Mappa ID esperto a label esperto
expert_map = {exp["id"]: exp["label"].get(lang_code, exp["label"]["EN"]) for exp in EXPERTS_CONFIG}

def format_date_locale(iso_string, l_code):
    try:
        dt = datetime.datetime.fromisoformat(iso_string)
        # Force Euro format universally to avoid American/ISO mismatches.
        return dt.strftime("%d/%m/%Y %H:%M")
    except:
        return iso_string.replace("T", " ")[:16]

# Sorting sempre descrescente base data ISO
sorted_chats = sorted(all_chats, key=lambda x: x.get("data_creazione_chat", ""), reverse=True)

# Popoliamo la griglia per espansione
for chat in sorted_chats:
    raw_date = chat.get("data_creazione_chat", "")
    date_formatted = format_date_locale(raw_date, lang_code)
        
    expert_id = chat.get("esperto", "Sconosciuto")
    expert_name = expert_map.get(expert_id, expert_id)
    chat_email = chat.get("email", "N/A")
    chat_nome = chat.get("nome", "").strip()
    chat_cognome = chat.get("cognome", "").strip()
    
    # Priority: nome+cognome from chat obj -> mapped name from activist list -> email
    display_name = ""
    # Se NON sei un organizzatore, non ha senso che tu legga il tuo nome/mail da tutte le parti sulle TUE chat.
    if "org" not in user_profiles:
        titolo = f"{date_formatted} - ({expert_name})"
    else:
        if chat_nome or chat_cognome:
            display_name = f"{chat_nome} {chat_cognome}".strip()
        elif chat_email in activist_map:
            display_name = activist_map[chat_email]
        else:
            display_name = chat_email
            
        titolo = f"{date_formatted} - {display_name} ({expert_name})"
    
    with st.expander(titolo):
        messages = chat.get("messages", [])
        if not messages:
            st.write("Nessun messaggio registrato.")
        
        # Rendiamo ogni messaggio uno per uno come chat
        for msg in messages:
            role = msg.get("ruolo", "user")
            content = msg.get("messaggio", "")
            
            with st.chat_message(role):
                st.markdown(content)
