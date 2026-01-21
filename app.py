import streamlit as st
from groq import Groq
import google.generativeai as genai
from openai import OpenAI  # <--- NEW: Libreria standard per OpenRouter
import json
import os
import re

# --- 1. FUNZIONI DI CARICAMENTO ---
def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Default di sicurezza
        if "app_config" in filepath:
            return {"provider": "groq", "model_name": "llama-3.3-70b-versatile", "temperature": 0.6}
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
        /* 1. PULIZIA VISIVA */
        [data-testid="stDecoration"] { display: none; } 
        footer { display: none; }

        /* 2. STILE MESSAGGI */
        [data-testid="stChatMessage"]:nth-child(odd) { 
            background-color: #111111; 
            border: 1px solid #333; 
        }
        [data-testid="stChatMessage"]:nth-child(even) { 
            background-color: #000000; 
            border: 1px solid #555; 
        }

        /* 3. INPUT CHAT */
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
    initial_sidebar_state="expanded"
)
apply_custom_css()

# Caricamento configurazioni
UI = load_json("data/ui.json")
EXPERTS_CONFIG = load_json("data/experts.json")
APP_CONFIG = load_json("data/app_config.json") 

# --- GESTIONE PROVIDER E API KEY ---
provider = APP_CONFIG.get("provider", "groq").lower()
model_name = APP_CONFIG.get("model_name", "llama-3.3-70b-versatile")

api_key = None
client_groq = None
client_openrouter = None # <--- NEW

# LOGICA DI INIZIALIZZAZIONE CLIENT
if provider == "groq":
    try:
        api_key = st.secrets["GROQ_API_KEY"]
    except:
        api_key = st.text_input("Groq API Key (gsk_...)", type="password")
    
    if api_key:
        client_groq = Groq(api_key=api_key)

elif provider == "gemini":
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
    except:
        api_key = st.text_input("Google API Key (AIza...)", type="password")
    
    if api_key:
        genai.configure(api_key=api_key)

# --- NEW: LOGICA OPENROUTER ---
elif provider == "openrouter":
    try:
        api_key = st.secrets["OPENROUTER_API_KEY"]
    except:
        api_key = st.text_input("OpenRouter API Key (sk-or-...)", type="password")
    
    if api_key:
        # OpenRouter usa la libreria OpenAI ma con un Base URL diverso
        client_openrouter = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://av-assistant.streamlit.app", # Opzionale: per le statistiche
                "X-Title": "AV Assistant"
            }
        )

if not api_key:
    st.warning(f"Inserisci la API Key per {provider.upper()} per continuare.")
    st.stop()


# Gestione Lingua
query_params = st.query_params
lang_code = query_params.get("lang", "EN").upper()
if lang_code not in ["IT", "EN"]: lang_code = "EN"
lang_idx = 0 if lang_code == "IT" else 1

ui_text = UI.get(lang_code, UI["EN"])

with st.sidebar:
    if os.path.exists("assets/logo.webp"):
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

    # --- RENDER SETTINGS (GENERICO) ---
    prompt_placeholders = {}
    if "settings" in current_expert:
        st.markdown("---")
        for setting in current_expert["settings"]:
            widget_label = setting["label"].get(lang_code, setting["label"]["EN"])
            opts_map = {opt["label"]: opt["value"] for opt in setting["options"]}
            selection = st.selectbox(widget_label, list(opts_map.keys()), key=f"set_{setting['key']}")
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

# Iniezione variabili dinamiche
for key, value in prompt_placeholders.items():
    final_system_instruction = final_system_instruction.replace(key, value)
    
final_system_instruction = re.sub(r"\{\{.*?\}\}", "", final_system_instruction)        

# --- 5. CHAT ENGINE (MULTI-PROVIDER) ---
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

    with st.chat_message("assistant"):
        stream_box = st.empty()
        full_res = ""
        
        try:
            # === LOGICA GROQ ===
            if provider == "groq":
                messages_payload = [{"role": "system", "content": final_system_instruction}]
                messages_payload.extend([{"role": m["role"], "content": m["content"]} for m in st.session_state.messages])
                
                completion = client_groq.chat.completions.create(
                    model=model_name, 
                    messages=messages_payload,
                    temperature=APP_CONFIG.get("temperature", 0.6),
                    max_tokens=1024,
                    stream=True,
                )

                for chunk in completion:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_res += content
                        stream_box.markdown(full_res)

            # === NEW: LOGICA OPENROUTER ===
            # OpenRouter usa lo stesso formato messaggi di Groq/OpenAI
            elif provider == "openrouter":
                messages_payload = [{"role": "system", "content": final_system_instruction}]
                messages_payload.extend([{"role": m["role"], "content": m["content"]} for m in st.session_state.messages])
                
                completion = client_openrouter.chat.completions.create(
                    model=model_name, 
                    messages=messages_payload,
                    temperature=APP_CONFIG.get("temperature", 0.6),
                    # Opzionali per OpenRouter
                    # top_p=1,
                    # frequency_penalty=0,
                    # presence_penalty=0,
                    stream=True,
                )

                for chunk in completion:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_res += content
                        stream_box.markdown(full_res)

            # === LOGICA GEMINI ===
            elif provider == "gemini":
                model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=final_system_instruction
                )
                
                gemini_history = []
                for msg in st.session_state.messages[:-1]:
                    role = "user" if msg["role"] == "user" else "model"
                    gemini_history.append({"role": role, "parts": [msg["content"]]})
                
                chat = model.start_chat(history=gemini_history)
                response = chat.send_message(prompt, stream=True)
                
                for chunk in response:
                    if chunk.text:
                        full_res += chunk.text
                        stream_box.markdown(full_res)

            # Salvataggio risposta
            st.session_state.messages.append({"role": "assistant", "content": full_res})
            
        except Exception as e:
            st.error(f"Errore {provider.upper()}: {e}")