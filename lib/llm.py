"""
Chiamata LLM singola e non-streaming, per le funzioni AI fuori dalla chat
(es. bozza di una scheda obiezione in pages/7_Gestione_Obiezioni.py).

Usa gli stessi provider e le stesse API key di pages/chat.py (Groq / OpenRouter / Gemini,
chiavi in `st.secrets`). La chat mantiene la sua logica streaming: qui serve solo
una risposta completa da elaborare.
"""

import json

import streamlit as st

SECRET_KEYS = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}


class LLMError(Exception):
    pass


def load_app_config():
    try:
        with open("data/app_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.6}


def resolve_model(feature_key=None):
    """(provider, model_name, temperature): override `app_config[feature_key]` se presente,
    altrimenti i default globali di app_config.json."""
    cfg = load_app_config()
    override = cfg.get(feature_key, {}) if feature_key else {}
    provider = (override.get("provider") or cfg.get("provider", "groq")).lower()
    model = override.get("model_name") or cfg.get("model_name", "openai/gpt-oss-120b")
    temperature = override.get("temperature", cfg.get("temperature", 0.6))
    return provider, model, temperature


def _api_key(provider):
    secret = SECRET_KEYS.get(provider)
    if not secret:
        raise LLMError(f"unknown_provider:{provider}")
    try:
        key = st.secrets.get(secret)
    except Exception:
        key = None
    if not key:
        raise LLMError(f"missing_api_key:{secret}")
    return key


def complete(system, user, feature_key=None, max_tokens=4096):
    """Ritorna il testo della risposta. Solleva LLMError per config mancante o errori del provider."""
    provider, model, temperature = resolve_model(feature_key)
    key = _api_key(provider)
    try:
        if provider in ("groq", "openrouter"):
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if provider == "groq":
                from groq import Groq
                client = Groq(api_key=key)
            else:
                from openai import OpenAI
                client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=key,
                    default_headers={"HTTP-Referer": "https://av-assistant.streamlit.app", "X-Title": "AV Assistant"},
                )
            res = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            )
            return res.choices[0].message.content or ""

        from google import genai
        client = genai.Client(api_key=key)
        res = client.models.generate_content(
            model=model,
            contents=[{"role": "user", "parts": [{"text": user}]}],
            config={"system_instruction": system, "temperature": temperature},
        )
        return res.text or ""
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"{provider}: {e}") from e


def extract_json(text):
    """Estrae il primo oggetto JSON dalla risposta (tollera ```json fences e testo attorno)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("invalid_json")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid_json: {e}") from e
