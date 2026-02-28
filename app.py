import streamlit as st
from groq import Groq
import google.generativeai as genai
from openai import OpenAI  # <--- NEW: Libreria standard per OpenRouter
import json
import extra_streamlit_components as stx
import uuid
import time
import datetime
import os
import re
import firebase_admin
from firebase_admin import credentials, firestore

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

# --- 1.1 FIREBASE INITIALIZATION ---
if not firebase_admin._apps:
    try:
        fb_creds = dict(st.secrets["firebase"])
        if "\\n" in fb_creds["private_key"]:
             fb_creds["private_key"] = fb_creds["private_key"].replace("\\n", "\n")
        
        cred = credentials.Certificate(fb_creds)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Errore inizializzazione Firebase: {e}")

db = firestore.client()
CHATS_COLLECTION = "active_chats"

def save_chat_to_server(user_id, current_expert_id):
    settings = {k: v for k, v in st.session_state.items() if k.startswith("set_")}
    
    data = {
        "timestamp": time.time(),
        "messages": st.session_state.messages,
        "expert_id": current_expert_id,
        "settings": settings
    }
    
    try:
        doc_ref = db.collection(CHATS_COLLECTION).document(user_id)
        doc_ref.set(data)
    except Exception as e:
        print(f"DEBUG: Error saving to Firebase: {e}")

def load_chat_from_server(user_id):
    print(f"DEBUG: Tring to load from Firebase collection {CHATS_COLLECTION}, doc {user_id}")
    try:
        doc_ref = db.collection(CHATS_COLLECTION).document(user_id)
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            print(f"DEBUG: Loaded {len(data.get('messages', []))} messages.")
            return data
        else:
            print("DEBUG: Document not found in Firebase.")
    except Exception as e:
        print(f"DEBUG: Error loading from Firebase: {e}")
    return None
    
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

if "messages" not in st.session_state:
    st.session_state.messages = []

# --- 2.1 COOKIE & SESSION MANAGEMENT ---
cookie_manager = stx.CookieManager(key="cookie_manager")
uuid_cookie = cookie_manager.get(cookie="av_user_id")

print(f"DEBUG: Cookie read result: {uuid_cookie} (Type: {type(uuid_cookie)})")

if uuid_cookie:
    print(f"DEBUG: Using existing cookie: {uuid_cookie}")
    st.session_state['user_id'] = uuid_cookie
    
    # Check consistency: Cookie exists, but does document exist?
    doc_ref = db.collection(CHATS_COLLECTION).document(uuid_cookie)
    if not doc_ref.get().exists:
        print(f"DEBUG: Document for cookie {uuid_cookie} missing. Re-creating.")
        if EXPERTS_CONFIG:
            save_chat_to_server(uuid_cookie, EXPERTS_CONFIG[0]["id"])
        else:
            st.error("Configurazione esperti mancante.")
            st.stop()
else:
    print("DEBUG: Cookie not found or None.")
    # Se il cookie non c'è, controlliamo se dobbiamo aspettare
    if "cookie_init_done" not in st.session_state:
        st.session_state["cookie_init_done"] = True
        print("DEBUG: Performing initial rerun for cookie manager...")
        time.sleep(0.5) 
        st.rerun()
        
    # Se siamo qui, dopo il rerun il cookie è ancora assente. Creiamone uno nuovo.
    new_uuid = str(uuid.uuid4())
    print(f"DEBUG: Generating NEW UUID: {new_uuid}")
    
    # Imposta cookie
    expires = datetime.datetime.now() + datetime.timedelta(days=30)
    cookie_manager.set("av_user_id", new_uuid, expires_at=expires)
    
    st.session_state['user_id'] = new_uuid
    
    # Create document for new user immediately
    if EXPERTS_CONFIG:
        save_chat_to_server(new_uuid, EXPERTS_CONFIG[0]["id"]) 
    else:
        st.error("Configurazione esperti mancante.")
        st.stop()

user_id = st.session_state['user_id']

# --- STATE RESTORATION ---
# Se la chat è vuota, prova a recuperare dal server
if not st.session_state.messages:
    saved_data = load_chat_from_server(user_id)
    if saved_data:
        st.session_state.messages = saved_data.get("messages", [])
        st.session_state.restored_expert_id = saved_data.get("expert_id")
        
        # Restore settings
        saved_settings = saved_data.get("settings", {})
        for k, v in saved_settings.items():
            st.session_state[k] = v



# Configurazione globale (fallback)
global_provider = APP_CONFIG.get("provider", "groq").lower()
global_model = APP_CONFIG.get("model_name", "llama-3.3-70b-versatile")


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

    # --- GOOGLE AUTHENTICATION ---
    # DEBUG SEZIONE
    # st.sidebar.write(f"DEBUG Auth Object: {st.user}")
    # st.sidebar.write(f"DEBUG Auth Email: {getattr(st.user, 'email', 'N/A')}")
    
    # Check for login status (st.user in 1.42+ handles this)
    try:
        is_logged_in = st.user.is_logged_in
    except AttributeError:
        # Fallback for older/different versions
        is_logged_in = st.user.get("email") is not None if hasattr(st.user, "get") else False

    if "code" in st.query_params and not is_logged_in:
        st.sidebar.warning("Autenticazione in corso... ricarica se bloccato.")
        if st.sidebar.button("Ricarica Forza"):
            st.rerun()

    if not is_logged_in:
        if st.button("🔑 Login with Google", use_container_width=True):
            st.login("google")
    else:
        user_email = getattr(st.user, 'email', 'User')
        st.write(f"Logged in as: **{user_email}**")
        if st.button("🚪 Logout", use_container_width=True):
            st.logout()

    st.markdown("---")
    
    # Mappa Esperti (filtrata per autenticazione)
    expert_options = {}
    
    for exp in EXPERTS_CONFIG:
        # Se l'esperto è publico O l'utente è autenticato
        if not exp.get("authorizedOnly", False) or is_logged_in:
            label = f"{exp['icon']} {exp['label'][lang_code]}"
            expert_options[label] = exp

    if not expert_options:
        st.warning("Nessun esperto disponibile.")
        st.stop()

    
    # Determine default index based on restored state
    default_index = 0
    expert_keys = list(expert_options.keys())
    
    if "restored_expert_id" in st.session_state:
        for idx, (label, exp) in enumerate(expert_options.items()):
            if exp["id"] == st.session_state.restored_expert_id:
                default_index = idx
                break

    selected_label = st.radio(ui_text["select_expert"], expert_keys, index=default_index)
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
        save_chat_to_server(user_id, current_expert["id"])
        st.rerun()

# --- GESTIONE PROVIDER E MODELLO (PER ESPERTO) ---
# Priorità: 1. Configurazione Esperto -> 2. Configurazione Globale
provider = current_expert.get("provider", global_provider).lower()
model_name = current_expert.get("model_name", global_model)

api_key = None
client_groq = None
client_openrouter = None

# LOGICA DI INIZIALIZZAZIONE CLIENT (Dinamica)
if provider == "groq":
    try:
        api_key = st.secrets.get("GROQ_API_KEY")
    except:
        pass
    if not api_key:
        api_key = st.sidebar.text_input("Groq API Key (gsk_...)", type="password")
    
    if api_key:
        client_groq = Groq(api_key=api_key)

elif provider == "gemini":
    try:
        api_key = st.secrets.get("GOOGLE_API_KEY")
    except:
        pass
    if not api_key:
        api_key = st.sidebar.text_input("Google API Key (AIza...)", type="password")
    
    if api_key:
        genai.configure(api_key=api_key)

elif provider == "openrouter":
    try:
        api_key = st.secrets.get("OPENROUTER_API_KEY")
    except:
        pass
    if not api_key:
        api_key = st.sidebar.text_input("OpenRouter API Key (sk-or-...)", type="password")
    
    if api_key:
        client_openrouter = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://av-assistant.streamlit.app",
                "X-Title": "AV Assistant"
            }
        )

if not api_key:
    st.warning(f"⚠️ {ui_text.get('api_key_required', 'API Key required')} ({provider.upper()})")
    st.stop()

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
                    if chunk.choices and chunk.choices[0].delta.content:
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
                    if chunk.choices and chunk.choices[0].delta.content:
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
            
            # --- SAVE STATE ---
            save_chat_to_server(user_id, current_expert["id"])
            
        except Exception as e:
            st.error(f"Errore {provider.upper()}: {e}")