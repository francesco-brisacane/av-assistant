import streamlit as st
from groq import Groq
import json
import os

# --- 1. FUNZIONI DI CARICAMENTO ---
def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"File non trovato: {filepath}")
        return {}

def load_text(filepath):
    if not filepath or not os.path.exists(filepath):
        return ""
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()
    
def apply_custom_css():
    st.markdown("""
        <style>
        /* 1. PULIZIA VISIVA (Senza toccare l'header!) */
        stAppToolbar { display: none; }
        /* Nasconde la riga colorata in alto */
        [data-testid="stDecoration"] { display: none; } 
        
        /* Nasconde il menu hamburger a destra e github
        [data-testid="stToolbar"] { display: none; }  */
        
        /* Nasconde il footer */
        footer { display: none; }

        /* 2. STILE MESSAGGI */
        /* Forziamo i colori dei messaggi per coerenza */
        [data-testid="stChatMessage"]:nth-child(odd) { 
            background-color: #111111; 
            border: 1px solid #333; 
        }
        [data-testid="stChatMessage"]:nth-child(even) { 
            background-color: #000000; 
            border: 1px solid #555; 
        }

        /* 3. INPUT CHAT (Opzionale, se config.toml non basta) */
        .stChatInput textarea {
            background-color: #111111 !important;
            color: white !important;
            border: 1px solid #333 !important;
        }
        </style>
        """, unsafe_allow_html=True)    
    
# --- 2. SETUP BASE ---
st.set_page_config(
    page_title="AV Assistant", 
    page_icon="🌱", 
    layout="centered",
    initial_sidebar_state="expanded"  # <--- AGGIUNGI QUESTA RIGA
)
apply_custom_css()

# Caricamento configurazioni
UI = load_json("data/ui.json")
EXPERTS_CONFIG = load_json("data/experts.json")

# Gestione API Key Groq
try:
    api_key = st.secrets["GROQ_API_KEY"]
except:
    # Fallback per input manuale se secrets non esiste
    api_key = st.text_input("Groq API Key (gsk_...)", type="password")

if not api_key:
    st.warning("Inserisci la Groq API Key per continuare.")
    st.stop()

# Inizializza Client Groq
client = Groq(api_key=api_key)

# Gestione Lingua
query_params = st.query_params
lang_code = query_params.get("lang", "EN").upper()
if lang_code not in ["IT", "EN"]: lang_code = "EN"
lang_idx = 0 if lang_code == "IT" else 1

ui_text = UI.get(lang_code, UI["EN"])

with st.sidebar:
    st.image("assets/logo.webp", width=80)
    
    new_lang = st.selectbox("Language", ["IT", "EN"], index=lang_idx)
    if new_lang != lang_code:
        st.query_params["lang"] = new_lang
        st.rerun()

    st.markdown("---")
    
    # Mappa Esperti
    expert_options = {}
    for exp in EXPERTS_CONFIG:
        label = f"{exp['icon']} {exp['label'][lang_code]}"
        expert_options[label] = exp

    selected_label = st.radio(ui_text["select_expert"], list(expert_options.keys()))
    current_expert = expert_options[selected_label]

# --- 4. RENDER SETTINGS (GENERICO) ---
    # Questa mappa conterrà le sostituzioni da fare nel prompt (es. {{difficulty}} -> "Sei ostile...")
    prompt_placeholders = {}

    # Se l'esperto ha delle impostazioni extra nel JSON, le creiamo qui
    if "settings" in current_expert:
        st.markdown("---")
        for setting in current_expert["settings"]:
            # Titolo del widget
            widget_label = setting["label"].get(lang_code, setting["label"]["EN"])
            
            # Creiamo le opzioni per il widget
            # Mappa: "Nome Visualizzato" -> "Valore da iniettare nel prompt"
            opts_map = {opt["label"]: opt["value"] for opt in setting["options"]}
            
            # Render del widget (Selectbox)
            # Usiamo un key univoco per evitare conflitti
            selection = st.selectbox(widget_label, list(opts_map.keys()), key=f"set_{setting['key']}")
            
            # Salviamo il valore pronto per essere iniettato
            # Esempio: prompt_placeholders["{{difficulty}}"] = "Sei un passante ostile..."
            prompt_placeholders[f"{{{{{setting['key']}}}}}"] = opts_map[selection]    

    if current_expert.get("disclaimer"):
        disclaimer_text = current_expert["disclaimer"].get(lang_code)
        if disclaimer_text:
            st.warning(disclaimer_text)

    if st.button(ui_text["clear_chat"]):
        st.session_state.messages = []
        st.rerun()

# --- 4. COSTRUZIONE PROMPT ---
st.title(selected_label)

raw_prompt = load_text(current_expert["prompt_file"])
lang_name = "Italiano" if lang_code == "IT" else "English"
final_system_instruction = raw_prompt.replace("{{LANGUAGE}}", lang_name)

if current_expert.get("kb_file"):
    kb_content = load_text(current_expert["kb_file"])
    if kb_content:
        final_system_instruction += f"\n\nCONTEXT / KNOWLEDGE BASE:\n{kb_content}"

# --- INIEZIONE VARIABILI DINAMICHE ---
# Qui applichiamo le scelte fatte nella sidebar (es. Difficulty) al testo del prompt
for key, value in prompt_placeholders.items():
    final_system_instruction = final_system_instruction.replace(key, value)
    
# Se nel prompt ci sono ancora placeholder non sostituiti (perché magari l'utente non ha settings), li puliamo
# Questo evita che nel prompt rimanga scritto "{{difficulty}}"
import re
final_system_instruction = re.sub(r"\{\{.*?\}\}", "", final_system_instruction)        

# --- 5. CHAT ENGINE (GROQ VERSION) ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# Reset chat al cambio esperto
if "last_expert_id" not in st.session_state:
    st.session_state.last_expert_id = current_expert["id"]

if st.session_state.last_expert_id != current_expert["id"]:
    st.session_state.messages = []
    st.session_state.last_expert_id = current_expert["id"]
    st.rerun()

# Render Messaggi
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Input Utente
placeholder = f"{ui_text['input_placeholder']} ({current_expert['label'][lang_code]})"
if prompt := st.chat_input(placeholder):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Costruzione Payload per Llama 3
    # 1. Messaggio di sistema
    messages_payload = [{"role": "system", "content": final_system_instruction}]
    # 2. Storia della chat
    messages_payload.extend([{"role": m["role"], "content": m["content"]} for m in st.session_state.messages])

    with st.chat_message("assistant"):
        stream_box = st.empty()
        full_res = ""
        
        try:
            # Chiamata a Groq (Llama 3.3 70B Versatile è ottimo e gratis)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile", 
                messages=messages_payload,
                temperature=0.6,
                max_tokens=1024,
                stream=True,
                stop=None,
            )

            for chunk in completion:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_res += content
                    stream_box.markdown(full_res)
            
            st.session_state.messages.append({"role": "assistant", "content": full_res})
            
        except Exception as e:
            st.error(f"Errore Groq: {e}")