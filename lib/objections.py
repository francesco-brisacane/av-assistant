"""
Catalogo condiviso delle obiezioni da strada e relative risposte.

Usato da:
  - pages/6_Obiezioni.py           (attivisti/org/admin: studio, ricerca, richieste)
  - pages/7_Gestione_Obiezioni.py  (org/admin: CRUD catalogo, evasione richieste)

Collezioni Firestore:
  - `objections/{id}` — una scheda per lingua. Schema:
      { lang: IT|EN, category, question, variants[], short_answer, socratic_questions[],
        body_md, pitfalls, sources[], status: published|archived, seed_id?, source_request_id?,
        created_by, updated_by, created_at, updated_at }
  - `objection_requests/{id}` — obiezione proposta da un attivista che chiede una risposta condivisa.
      { lang, text, context, requested_by, requested_by_name, status: open|answered|merged|rejected,
        objection_id, resolver_note, resolved_by, resolved_at, created_at }

Il catalogo e' unico per tutta AV (non per capitolo): ogni org puo' aggiungere e modificare,
solo l'admin elimina definitivamente (gli org archiviano).

La ricerca e' fatta in Python sull'intero catalogo della lingua: Firestore non ha full-text
e il catalogo resta nell'ordine delle centinaia di schede.
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher, get_close_matches

import streamlit as st
from firebase_admin import firestore

OBJECTIONS_COLLECTION = "objections"
REQUESTS_COLLECTION = "objection_requests"
SEED_FILE = "data/objections_seed.json"

# Le label sono in i18n.json come `obj_cat_<key>`.
CATEGORIES = [
    "nature",
    "health",
    "humane",
    "animals",
    "plants",
    "choice",
    "tradition",
    "practical",
    "impact",
    "whataboutism",
    "other",
]

REQUEST_STATUSES = ["open", "answered", "merged", "rejected"]

# Parole troppo comuni per contare nel matching (IT + EN).
_STOPWORDS = {
    "che", "non", "per", "una", "uno", "gli", "le", "lo", "il", "la", "di", "da", "del", "della",
    "dei", "delle", "con", "come", "anche", "pero", "ma", "se", "sono", "sei", "e", "ed", "piu",
    "tutti", "tutto", "questo", "quello", "cosa", "mai", "gia", "poi", "solo", "loro", "noi", "voi",
    "mi", "ti", "si", "ci", "vi", "io", "tu", "lui", "lei", "nel", "nella", "sul", "sulla", "allora",
    "the", "and", "but", "you", "are", "for", "not", "with", "that", "this", "what", "they", "them",
    "have", "has", "its", "was", "all", "also", "just", "your", "our", "from", "too",
}


# ---------------------------------------------------------------------------
# Ricerca
# ---------------------------------------------------------------------------

def normalize(text):
    """Minuscolo, senza accenti e punteggiatura, spazi compattati."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def _tokens(text):
    return [w for w in normalize(text).split() if len(w) > 2 and w not in _STOPWORDS]


def _token_coverage(q_tokens, target_tokens):
    """Frazione dei token della query trovati nel testo (esatti, per prefisso o con typo)."""
    if not q_tokens or not target_tokens:
        return 0.0
    target_set = set(target_tokens)
    hits = 0.0
    for q in q_tokens:
        if q in target_set:
            hits += 1
        elif len(q) > 3 and any(t.startswith(q) for t in target_set):
            hits += 0.9
        elif get_close_matches(q, target_set, n=1, cutoff=0.8):
            hits += 0.8
    return hits / len(q_tokens)


def score_objection(query, obj):
    """Punteggio 0..1 di quanto `obj` corrisponde a `query`.

    Obiezione e varianti pesano pieno; risposta breve e approfondimento a meta'
    (servono a trovare la scheda partendo da un concetto, non dalla frase esatta).
    """
    q_norm = normalize(query)
    if not q_norm:
        return 0.0
    q_tokens = _tokens(query) or q_norm.split()

    best = 0.0
    for text in [obj.get("question", "")] + list(obj.get("variants") or []):
        t_norm = normalize(text)
        if not t_norm:
            continue
        if f" {q_norm} " in f" {t_norm} ":
            return 1.0
        best = max(
            best,
            _token_coverage(q_tokens, _tokens(text)),
            SequenceMatcher(None, q_norm, t_norm).ratio() * 0.85,
        )

    secondary = " ".join([obj.get("short_answer", ""), obj.get("body_md", "")])
    best = max(best, _token_coverage(q_tokens, _tokens(secondary)) * 0.5)
    return best


def search_objections(query, objections, min_score=0.35, limit=None):
    """Ritorna le schede ordinate per pertinenza. Query vuota -> tutte, in ordine stabile."""
    if not normalize(query):
        return sorted(objections, key=lambda o: (CATEGORIES.index(o.get("category")) if o.get("category") in CATEGORIES else 99, normalize(o.get("question", ""))))
    scored = [(score_objection(query, o), o) for o in objections]
    scored = [(s, o) for s, o in scored if s >= min_score]
    scored.sort(key=lambda x: x[0], reverse=True)
    result = [o for _, o in scored]
    return result[:limit] if limit else result


# ---------------------------------------------------------------------------
# Firestore: helpers
# ---------------------------------------------------------------------------

def _ts_to_iso(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _doc_to_dict(doc):
    d = doc.to_dict() or {}
    d["id"] = doc.id
    for k in ("created_at", "updated_at", "resolved_at"):
        if k in d:
            d[k] = _ts_to_iso(d[k])
    return d


def split_lines(text):
    """Textarea 'uno per riga' -> lista pulita."""
    return [line.strip().lstrip("-•*").strip() for line in (text or "").splitlines() if line.strip()]


def clear_caches():
    load_objections.clear()
    load_open_requests_count.clear()


# ---------------------------------------------------------------------------
# Firestore: schede
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120, show_spinner=False)
def load_objections(_db, lang=None, include_archived=False):
    """Carica le schede (filtrate per lingua se indicata). Il parametro `_db` non e' hashato."""
    query = _db.collection(OBJECTIONS_COLLECTION)
    if lang:
        query = query.where("lang", "==", lang)
    items = [_doc_to_dict(d) for d in query.stream()]
    if not include_archived:
        items = [o for o in items if o.get("status", "published") == "published"]
    return items


def save_objection(db, data, editor_email, objection_id=None):
    """Crea o aggiorna una scheda. Ritorna l'id."""
    payload = {
        "lang": data.get("lang", "IT"),
        "category": data.get("category", "other"),
        "question": (data.get("question") or "").strip(),
        "variants": data.get("variants") or [],
        "short_answer": (data.get("short_answer") or "").strip(),
        "socratic_questions": data.get("socratic_questions") or [],
        "body_md": (data.get("body_md") or "").strip(),
        "pitfalls": (data.get("pitfalls") or "").strip(),
        "sources": data.get("sources") or [],
        "status": data.get("status", "published"),
        "updated_by": editor_email,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    if objection_id:
        db.collection(OBJECTIONS_COLLECTION).document(objection_id).update(payload)
    else:
        payload["created_by"] = editor_email
        payload["created_at"] = firestore.SERVER_TIMESTAMP
        if data.get("source_request_id"):
            payload["source_request_id"] = data["source_request_id"]
        _, ref = db.collection(OBJECTIONS_COLLECTION).add(payload)
        objection_id = ref.id
    clear_caches()
    return objection_id


def set_objection_status(db, objection_id, status, editor_email):
    db.collection(OBJECTIONS_COLLECTION).document(objection_id).update({
        "status": status,
        "updated_by": editor_email,
        "updated_at": firestore.SERVER_TIMESTAMP,
    })
    clear_caches()


def delete_objection(db, objection_id):
    db.collection(OBJECTIONS_COLLECTION).document(objection_id).delete()
    clear_caches()


def import_seed(db, editor_email, path=SEED_FILE):
    """Importa i contenuti iniziali. Idempotente: id documento = `seed_<seed_id>`,
    le schede gia' presenti (anche archiviate o modificate) non vengono toccate.
    Ritorna (importate, gia_presenti)."""
    with open(path, "r", encoding="utf-8") as f:
        seed = json.load(f)
    imported, skipped = 0, 0
    for item in seed:
        doc_id = f"seed_{item['seed_id']}"
        ref = db.collection(OBJECTIONS_COLLECTION).document(doc_id)
        if ref.get().exists:
            skipped += 1
            continue
        ref.set({
            "lang": item["lang"],
            "category": item.get("category", "other"),
            "question": item["question"],
            "variants": item.get("variants", []),
            "short_answer": item.get("short_answer", ""),
            "socratic_questions": item.get("socratic_questions", []),
            "body_md": item.get("body_md", ""),
            "pitfalls": item.get("pitfalls", ""),
            "sources": item.get("sources", []),
            "status": "published",
            "seed_id": item["seed_id"],
            "created_by": editor_email,
            "updated_by": editor_email,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        imported += 1
    clear_caches()
    return imported, skipped


# ---------------------------------------------------------------------------
# Firestore: richieste
# ---------------------------------------------------------------------------

def create_request(db, text, context, lang, email, name):
    db.collection(REQUESTS_COLLECTION).add({
        "lang": lang,
        "text": text.strip(),
        "context": (context or "").strip(),
        "requested_by": email,
        "requested_by_name": name or "",
        "status": "open",
        "objection_id": None,
        "resolver_note": "",
        "resolved_by": None,
        "resolved_at": None,
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    load_open_requests_count.clear()


def load_requests(db, requested_by=None, status=None):
    """Richieste filtrate per autore o per stato (un solo filtro -> nessun indice composito).
    Ordinate dalla piu' recente."""
    query = db.collection(REQUESTS_COLLECTION)
    if requested_by:
        query = query.where("requested_by", "==", requested_by)
    elif status:
        query = query.where("status", "==", status)
    items = [_doc_to_dict(d) for d in query.stream()]
    if requested_by and status:
        items = [r for r in items if r.get("status") == status]
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return items


@st.cache_data(ttl=120, show_spinner=False)
def load_open_requests_count(_db):
    return sum(1 for _ in _db.collection(REQUESTS_COLLECTION).where("status", "==", "open").stream())


def resolve_request(db, request_id, status, resolver_email, objection_id=None, note=""):
    db.collection(REQUESTS_COLLECTION).document(request_id).update({
        "status": status,
        "objection_id": objection_id,
        "resolver_note": (note or "").strip(),
        "resolved_by": resolver_email,
        "resolved_at": firestore.SERVER_TIMESTAMP,
    })
    load_open_requests_count.clear()


# ---------------------------------------------------------------------------
# UI condivisa
# ---------------------------------------------------------------------------

def category_label(key, t):
    return t(f"obj_cat_{key}", key)


def render_objection_body(obj, t):
    """Contenuto di una scheda (senza titolo), da mettere dentro un expander/container."""
    st.caption("🏷️ " + category_label(obj.get("category", "other"), t))

    if obj.get("short_answer"):
        st.markdown("**💡 " + t("obj_short_answer", "In breve") + "**")
        st.info(obj["short_answer"])

    if obj.get("socratic_questions"):
        st.markdown("**❓ " + t("obj_socratic_questions", "Domande da fare") + "**")
        st.markdown("\n".join(f"- {q}" for q in obj["socratic_questions"]))

    if obj.get("body_md"):
        st.markdown("**📖 " + t("obj_body", "Approfondimento") + "**")
        st.markdown(obj["body_md"])

    if obj.get("pitfalls"):
        st.markdown("**⚠️ " + t("obj_pitfalls", "Errori da evitare") + "**")
        st.warning(obj["pitfalls"])

    if obj.get("variants"):
        st.markdown("**🔁 " + t("obj_variants", "Si sente dire anche") + "**")
        st.markdown("\n".join(f"- _{v}_" for v in obj["variants"]))

    if obj.get("sources"):
        st.markdown("**🔗 " + t("obj_sources", "Fonti") + "**")
        st.markdown("\n".join(f"- {s}" for s in obj["sources"]))
