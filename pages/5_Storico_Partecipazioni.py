"""
Pagina "Storico Partecipazioni" — riservata org/admin.

Aggrega i dati di `cube_events` del capitolo per dare all'organizer:
  - Tabella per attivista: invitati, partecipato (yes), non risposto, % partecipazione,
    ultimo cubo partecipato, ultima attivita'.
  - Statistiche aggregate del capitolo.
  - Grafico trend mensile del tasso di partecipazione.
  - Esportazione CSV dei dati per analisi off-line.

Solo cubi con `poll_link` valorizzato sono considerati (gli altri non hanno dati
di partecipazione utili).
"""

import csv
import io
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import firebase_admin
import pandas as pd
import streamlit as st
from firebase_admin import firestore


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

if "org" not in user_profiles and "admin" not in user_profiles:
    st.error(t("access_denied", "Accesso negato. Questa pagina e' riservata agli organizer."))
    st.stop()

if not firebase_admin._apps:
    st.error(t("firebase_uninitialized", "Firebase non inizializzato correttamente dall'app principale."))
    st.stop()
db = firestore.client()


# ---------------------------------------------------------------------------
# UI: titolo
# ---------------------------------------------------------------------------

st.title(t("history_page_title", "Storico Partecipazioni"))
st.write(t("history_page_desc", "Vista aggregata dei cubi e dei tassi di partecipazione del capitolo.") + f" — **{user_email}**")


# ---------------------------------------------------------------------------
# Carica dati
# ---------------------------------------------------------------------------

org_ref = db.collection("organizers").document(user_email)
org_doc = org_ref.get()
org_data = org_doc.to_dict() if org_doc.exists else {}
activists = org_data.get("activists", [])

events_query = db.collection("cube_events").where("organizer_email", "==", user_email).stream()
all_events = []
for e in events_query:
    d = e.to_dict() or {}
    d["_id"] = e.id
    all_events.append(d)

# Filtra: solo cubi con sondaggio collegato (gli altri non hanno dati partecipazione)
events_with_poll = [e for e in all_events if e.get("poll_link")]


def event_date(e):
    try:
        return datetime.strptime(e.get("data", "") or "", "%Y-%m-%d").date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Filtri
# ---------------------------------------------------------------------------

col_f1, col_f2 = st.columns([3, 7])

period_options = [
    ("30", t("history_period_30d", "Ultimi 30 giorni")),
    ("90", t("history_period_90d", "Ultimi 90 giorni")),
    ("365", t("history_period_365d", "Ultimo anno")),
    ("all", t("history_period_all", "Tutto")),
]
with col_f1:
    period_choice = st.selectbox(
        t("history_filter_period", "Periodo"),
        options=[p[0] for p in period_options],
        format_func=lambda x: dict(period_options)[x],
        index=1,  # default 90 giorni
    )

today = date.today()
cutoff_map = {"30": 30, "90": 90, "365": 365, "all": None}
cutoff_days = cutoff_map[period_choice]
cutoff = (today - timedelta(days=cutoff_days)) if cutoff_days else None


def in_period(e):
    ed = event_date(e)
    if ed is None:
        return False
    if cutoff is None:
        return True
    return ed >= cutoff


events_filtered = [e for e in events_with_poll if in_period(e)]


# ---------------------------------------------------------------------------
# Statistiche capitolo
# ---------------------------------------------------------------------------

n_events = len(events_filtered)
n_activists = len(activists)

# Calcolo medie globali
total_yes_global = 0
total_invited_global = 0
total_voters_global = 0
for e in events_filtered:
    cat_by_idx = {o["idx"]: o.get("category", "ignore") for o in e.get("poll_options", [])}
    parts = e.get("participations", {})
    invited = n_activists  # snapshot dinamico
    yes_count = sum(1 for p in parts.values() if cat_by_idx.get(p.get("option_idx"), "ignore") == "yes")
    voters_count = len(parts)
    total_yes_global += yes_count
    total_invited_global += invited
    total_voters_global += voters_count

avg_participants = (total_yes_global / n_events) if n_events else 0
participation_rate = (total_yes_global / total_invited_global * 100) if total_invited_global else 0
response_rate = (total_voters_global / total_invited_global * 100) if total_invited_global else 0

st.markdown("### " + t("history_stats_title", "Statistiche del capitolo"))
col_s1, col_s2, col_s3, col_s4 = st.columns(4)
col_s1.metric(t("history_stat_events", "Cubi nel periodo"), n_events)
col_s2.metric(t("history_stat_avg_participants", "Media partecipanti / cubo"), f"{avg_participants:.1f}")
col_s3.metric(t("history_stat_participation_rate", "Tasso partecipazione"), f"{participation_rate:.1f}%")
col_s4.metric(t("history_stat_response_rate", "Tasso risposta sondaggio"), f"{response_rate:.1f}%")

if n_events == 0:
    st.info(t("history_no_events", "Nessun cubo con sondaggio nel periodo selezionato."))
    st.stop()


# ---------------------------------------------------------------------------
# Tabella per attivista
# ---------------------------------------------------------------------------

st.markdown("### " + t("history_table_title", "Per attivista"))


def compute_activist_stats(activist, events):
    email = activist.get("email", "")
    invited = 0
    participated = 0
    no_response = 0
    last_participation = None
    last_activity = None
    for e in events:
        invited += 1
        cat_by_idx = {o["idx"]: o.get("category", "ignore") for o in e.get("poll_options", [])}
        part = e.get("participations", {}).get(email)
        ed = event_date(e)
        if part:
            cat = cat_by_idx.get(part.get("option_idx"), "ignore")
            if cat == "yes":
                participated += 1
                if ed and (last_participation is None or ed > last_participation):
                    last_participation = ed
            if ed and (last_activity is None or ed > last_activity):
                last_activity = ed
        else:
            no_response += 1
    rate = (participated / invited * 100) if invited else 0
    return {
        "invited": invited,
        "participated": participated,
        "no_response": no_response,
        "rate": rate,
        "last_participation": last_participation,
        "last_activity": last_activity,
    }


rows = []
for a in activists:
    stats = compute_activist_stats(a, events_filtered)
    rows.append({
        t("history_col_name", "Nome"): a.get("nome", ""),
        t("history_col_surname", "Cognome"): a.get("cognome", ""),
        t("history_col_telegram", "Telegram"): ("@" + a["telegram_username"]) if a.get("telegram_username") else "—",
        t("history_col_invited", "Invitati"): stats["invited"],
        t("history_col_participated", "Partecipato"): stats["participated"],
        t("history_col_no_response", "Non risposto"): stats["no_response"],
        t("history_col_rate", "% Partecipazione"): round(stats["rate"], 1),
        t("history_col_last_participation", "Ultimo cubo"): stats["last_participation"].strftime("%Y-%m-%d") if stats["last_participation"] else "—",
        t("history_col_last_activity", "Ultima attivita'"): stats["last_activity"].strftime("%Y-%m-%d") if stats["last_activity"] else "—",
    })

if rows:
    df = pd.DataFrame(rows)
    # Sort per default: % partecipazione decrescente
    rate_col = t("history_col_rate", "% Partecipazione")
    df_sorted = df.sort_values(by=rate_col, ascending=False)
    st.dataframe(df_sorted, hide_index=True, use_container_width=True)

    # Export CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=df_sorted.columns.tolist())
    writer.writeheader()
    for r in df_sorted.to_dict(orient="records"):
        writer.writerow(r)
    csv_bytes = buf.getvalue().encode("utf-8-sig")  # BOM per Excel
    st.download_button(
        label="⬇️ " + t("history_btn_export_csv", "Esporta CSV"),
        data=csv_bytes,
        file_name=f"storico_partecipazioni_{user_email}_{period_choice}_{today.isoformat()}.csv",
        mime="text/csv",
    )
else:
    st.info(t("history_no_activists", "Nessun attivista nel capitolo."))


# ---------------------------------------------------------------------------
# Grafico trend mensile
# ---------------------------------------------------------------------------

st.markdown("### " + t("history_trend_title", "Andamento mensile"))

monthly = defaultdict(lambda: {"events": 0, "total_yes": 0, "total_invited": 0, "total_voters": 0})
for e in events_filtered:
    ed = event_date(e)
    if not ed:
        continue
    month_key = ed.strftime("%Y-%m")
    cat_by_idx = {o["idx"]: o.get("category", "ignore") for o in e.get("poll_options", [])}
    parts = e.get("participations", {})
    invited = n_activists
    yes_count = sum(1 for p in parts.values() if cat_by_idx.get(p.get("option_idx"), "ignore") == "yes")
    voters_count = len(parts)
    monthly[month_key]["events"] += 1
    monthly[month_key]["total_yes"] += yes_count
    monthly[month_key]["total_invited"] += invited
    monthly[month_key]["total_voters"] += voters_count

trend_rows = []
for month in sorted(monthly.keys()):
    m = monthly[month]
    rate = (m["total_yes"] / m["total_invited"] * 100) if m["total_invited"] else 0
    resp = (m["total_voters"] / m["total_invited"] * 100) if m["total_invited"] else 0
    trend_rows.append({
        "Mese": month,
        t("history_trend_y_rate", "Tasso partecipazione %"): round(rate, 1),
        t("history_trend_y_resp", "Tasso risposta sondaggio %"): round(resp, 1),
        t("history_trend_y_events", "Cubi nel mese"): m["events"],
    })

if trend_rows:
    df_trend = pd.DataFrame(trend_rows).set_index("Mese")
    rate_label = t("history_trend_y_rate", "Tasso partecipazione %")
    resp_label = t("history_trend_y_resp", "Tasso risposta sondaggio %")
    st.line_chart(df_trend[[rate_label, resp_label]])
    st.caption(t("history_trend_caption", "Tasso partecipazione = % attivisti che hanno votato 'Partecipa' sul totale invitati. Tasso risposta = % attivisti che hanno votato qualsiasi cosa."))
    with st.expander(t("history_trend_table_label", "Dati grezzi del grafico")):
        st.dataframe(df_trend, use_container_width=True)
else:
    st.info(t("history_no_trend_data", "Dati insufficienti per il grafico."))
