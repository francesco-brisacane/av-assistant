import streamlit as st
import json

def load_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

I18N = load_json("data/i18n.json")

# Initialize language early so Page titles can be translated
if "lang" not in st.session_state:
    query_params = st.query_params
    st.session_state["lang"] = query_params.get("lang", "EN").upper()

lang_code = st.session_state["lang"]
if lang_code not in ["IT", "EN"]: lang_code = "EN"
current_i18n = I18N.get(lang_code, I18N["EN"])

# Define Pages
chat_page = st.Page("pages/chat.py", title=current_i18n.get("menu_chat", "Chat"), icon="💬", default=True)
activists_page = st.Page("pages/1_I_Miei_Attivisti.py", title=current_i18n.get("menu_activists", "My Activists"), icon="👥")
organizers_page = st.Page("pages/2_Gestione_Organizzatori.py", title=current_i18n.get("menu_organizers", "Manage Organizers"), icon="⚙️")
chat_viewer_page = st.Page("pages/3_Chat_Attivisti.py", title=current_i18n.get("menu_chat_viewer", "Chat Attivisti"), icon="📑")

# Determine visibility based on user profiles
user_profiles = st.session_state.get("user_profiles", [])
is_logged_in = False
try:
    is_logged_in = st.user.is_logged_in
except AttributeError:
    is_logged_in = st.user.get("email") is not None if hasattr(st.user, "get") else False

# Navigation logic
pages = [chat_page]

if is_logged_in:
    if "org" in user_profiles or "admin" in user_profiles:
        pages.append(activists_page)
    if "admin" in user_profiles:
        pages.append(organizers_page)
    if "org" in user_profiles or "activist" in user_profiles:
        pages.append(chat_viewer_page)

pg = st.navigation(pages)
pg.run()
