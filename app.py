import streamlit as st
import google.generativeai as genai
import json
import os


def apply_custom_css():
    st.markdown("""
        <style>
        /* 1. Sfondo globale e testo */
        .stApp {
            background-color: #000000;
            color: #ffffff;
        }

        /* 2. Sidebar */
        [data-testid="stSidebar"] {
            background-color: #111111;
            border-right: 1px solid #333;
        }

        /* 3. Input Text */
        .stChatInput input {
            background-color: #111111 !important;
            color: white !important;
            border: 1px solid #333 !important;
        }
        
        /* 4. PULIZIA INTERFACCIA STREAMLIT (Stealth Mode) */
        
        /* Nasconde la Toolbar in alto a destra (Menu hamburger, GitHub icon, etc.) */
        [data-testid="stToolbar"] {
            visibility: hidden !important;
            display: none !important;
        }
        
        /* Nasconde la riga colorata decorativa */
        [data-testid="stDecoration"] {
            display: none;
        }
        
        /* Nasconde il bottone "Deploy" se visibile */
        .stDeployButton {
            display: none;
        }
        
        /* Header trasparente */
        header[data-testid="stHeader"] {
            background-color: transparent !important;
        }

        /* Nasconde footer "Made with Streamlit" */
        footer {visibility: hidden;}

        /* 5. GESTIONE FRECCETTA SIDEBAR */
        /* Fondamentale: Rendiamo visibile SOLO la freccetta per aprire/chiudere la sidebar */
        [data-testid="stSidebarCollapsedControl"] {
            display: block !important;
            color: #FFFFFF !important;
            visibility: visible !important;
        }
        
        /* Stile messaggi Chat */
        [data-testid="stChatMessage"]:nth-child(odd) {
            background-color: #111111;
            border: 1px solid #333;
            border-radius: 10px;
        }
        [data-testid="stChatMessage"]:nth-child(even) {
            background-color: #000000;
            border: 1px solid #ffffff;
            border-radius: 10px;
        }
        
        /* Radio Buttons */
        div[role="radiogroup"] > label > div:first-child {
            background-color: #333 !important;
            border-color: #fff !important;
        }
        </style>
        """, unsafe_allow_html=True)

# --- 1. SETUP E FUNZIONI UTILI ---
st.set_page_config(page_title="AV Assistant", page_icon="🌱")
apply_custom_css()

def load_text_file(filepath):
    """Legge file di testo (md, txt)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: File {filepath} not found."

def load_json_file(filepath):
    """Legge file JSON per le traduzioni."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"Critical Error: {filepath} not found.")
        st.stop()

# --- 2. GESTIONE LINGUA (QUERY PARAM + DEFAULT) ---
# Leggiamo i parametri dall'URL (es. ?lang=IT)
query_params = st.query_params
initial_lang_code = query_params.get("lang", "EN").upper() # Default "EN" se vuoto

# Validazione: se l'utente scrive ?lang=PIZZA, torniamo a EN
if initial_lang_code not in ["IT", "EN"]:
    initial_lang_code = "EN"

# Mapping indice per la selectbox (0=IT, 1=EN se l'ordine è [IT, EN])
# Nota: Dipende dall'ordine della lista nella selectbox più sotto
lang_index = 0 if initial_lang_code == "IT" else 1

# --- 3. CARICAMENTO DATI ---
UI_TEXT = load_json_file("data/ui.json")
knowledge_base = load_text_file("data/knowledge_base.txt")

# --- 4. API KEY ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
except:
    api_key = st.text_input("Google API Key", type="password")

if not api_key:
    st.stop()

genai.configure(api_key=api_key)

# --- 5. INTERFACCIA (SIDEBAR) ---
with st.sidebar:
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.image("https://images.squarespace-cdn.com/content/v1/5815cb5b197aea6c5f13a2d0/1556924489351-QDV744Z6QDVVVJH4O3XY/AV-Symbol-White-Transparent-Website.png?format=500w", use_container_width=True) # O usa use_column_width=True su versioni vecchie
    
    # Selettore Lingua (inizializzato col valore dell'URL)
    lang_code = st.selectbox(
        "Language", 
        ["IT", "EN"], 
        index=lang_index,
        key="lang_select" 
    )
    
    # Se cambia la lingua nella selectbox, aggiorniamo l'URL (UX opzionale ma carina)
    if lang_code != initial_lang_code:
        st.query_params["lang"] = lang_code

    t = UI_TEXT[lang_code] # Carica il dizionario della lingua corrente

    st.markdown("---")
    
    # Selettore Modalità
    mode_options = [t["mode_nutri"], t["mode_chef"]]
    selected_mode_label = st.radio(t["select_mode"], mode_options)
    
    # Mapping prompt
    if selected_mode_label == t["mode_nutri"]:
        current_prompt_file = "prompts/nutritionist.md"
    else:
        current_prompt_file = "prompts/chef.md"

    st.warning(f"**{t['disclaimer_title']}**\n\n{t['disclaimer_text']}")
    
    if st.button(t["clear_chat"]):
        st.session_state.messages = []
        st.rerun()
    
    st.caption(t.get("footer_credit", ""))

# --- 6. LOGICA CHAT ---

# Definiamo un'icona dinamica in base alla modalità
if selected_mode_label == t["mode_nutri"]:
    page_icon = "🧬" # O un'altra icona scientifica
else:
    page_icon = "🍳" # O un cappello da chef

# Titolo Dinamico: Icona + Nome della modalità scelta
st.title(f"{page_icon} {selected_mode_label}")

# Opzionale: Aggiungi un sottotitolo piccolo per il branding
st.caption("Anonymous for the Voiceless Assistant")

# Caricamento e preparazione Prompt
raw_prompt = load_text_file(current_prompt_file)
lang_full_name = "Italiano" if lang_code == "IT" else "English"
final_system_instruction = raw_prompt.replace("{{LANGUAGE}}", lang_full_name)

if selected_mode_label == t["mode_nutri"]:
    final_system_instruction += f"\n\nCONTEXT / KNOWLEDGE BASE:\n{knowledge_base}"

# Inizializzazione Chat
if "messages" not in st.session_state:
    st.session_state.messages = []

# Visualizzazione Cronologia
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input Utente
if prompt := st.chat_input(t["input_placeholder"]):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Chiamata API
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=final_system_instruction
    )
    
    gemini_history = []
    for msg in st.session_state.messages[:-1]:
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({"role": role, "parts": [msg["content"]]})

    with st.chat_message("assistant"):
        stream_box = st.empty()
        full_res = ""
        with st.spinner(t["loading"]):
            try:
                chat = model.start_chat(history=gemini_history)
                response = chat.send_message(prompt, stream=True)
                for chunk in response:
                    if chunk.text:
                        full_res += chunk.text
                        stream_box.markdown(full_res)
                st.session_state.messages.append({"role": "assistant", "content": full_res})
            except Exception as e:
                st.error(f"Error: {e}")
