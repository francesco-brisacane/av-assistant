"""
Modulo Telethon riutilizzabile per il progetto AV.

Responsabilita':
- Cifrare/decifrare la StringSession Telethon di ogni organizer (Fernet).
- Esporre il flow di login programmatico (numero -> codice -> eventuale 2FA).
- Operazioni read-only sui sondaggi: get_poll_message, get_poll_voters,
  resolve_username, parse_telegram_message_link.
- Invio DM reminder: send_dm (Fase 3).

Convenzioni:
- Non viene mantenuta nessuna istanza di TelegramClient cacheata fra rerun
  Streamlit: ogni operazione apre un client fresco da StringSession e lo
  chiude alla fine. Questo evita problemi con event loop chiusi.
- Tutte le funzioni "pubbliche" hanno una versione sync che incapsula
  asyncio.run(...) cosi' da poter essere chiamate direttamente dalle pagine
  Streamlit (che sono sincrone).
- I segreti vengono letti da st.secrets. Le funzioni che hanno bisogno
  di segreti li accettano comunque come parametri espliciti per facilitare
  i test.

Secrets richiesti in .streamlit/secrets.toml:

    TELEGRAM_API_ID = "12345"                  # numerico, da my.telegram.org
    TELEGRAM_API_HASH = "abc123..."            # stringa esadecimale
    TELEGRAM_SESSION_FERNET_KEY = "..."        # chiave Fernet (base64 44 char)

Per generare TELEGRAM_SESSION_FERNET_KEY una volta sola:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, List, Optional, Tuple, Union

import streamlit as st
from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    PeerIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    PollVoteRequiredError,
    SessionPasswordNeededError,
    UserIdInvalidError,
    UserIsBlockedError,
    UserPrivacyRestrictedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    YouBlockedUserError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetPollVotesRequest
from telethon.tl.types import MessageMediaPoll


# ---------------------------------------------------------------------------
# Tipi / Exceptions
# ---------------------------------------------------------------------------

class TelegramConfigError(RuntimeError):
    """Sollevata se mancano segreti / parametri di configurazione."""


class TelegramLoginError(RuntimeError):
    """Errore generico durante il flow di login (con messaggio user-friendly)."""


class TelegramOperationError(RuntimeError):
    """Errore durante una operazione Telethon post-login (lettura poll, DM, ecc.)."""


# ---------------------------------------------------------------------------
# Helper: parsing link e testi
# ---------------------------------------------------------------------------

# chat_ref puo' essere:
#   - str: username del gruppo pubblico (senza @)
#   - int: full channel ID per supergroup privato (es. -1001234567890)
ChatRef = Union[str, int]


def parse_telegram_message_link(url: str) -> Tuple[ChatRef, int]:
    """Estrae (chat_ref, message_id) da un link Telegram.

    Supporta:
    - https://t.me/<username>/<msg_id>            (gruppo/canale pubblico)
    - https://t.me/<username>/<topic>/<msg_id>    (forum/thread pubblico)
    - https://t.me/c/<internal>/<msg_id>          (supergroup privato)
    - https://t.me/c/<internal>/<topic>/<msg_id>  (forum privato)

    Per i privati ritorna full channel ID nel formato -100<internal>.
    """
    if not url:
        raise TelegramOperationError("Link sondaggio vuoto.")
    url = url.strip()
    # Caso privato: t.me/c/<internal>/<msg> oppure t.me/c/<internal>/<topic>/<msg>
    m_priv = re.match(
        r"^https?://t\.me/c/(\d+)(?:/\d+)?/(\d+)/?$",
        url,
    )
    if m_priv:
        internal_id = int(m_priv.group(1))
        msg_id = int(m_priv.group(2))
        full_channel_id = int(f"-100{internal_id}")
        return full_channel_id, msg_id
    # Caso pubblico: t.me/<username>/<msg> oppure con thread
    m_pub = re.match(
        r"^https?://t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})(?:/\d+)?/(\d+)/?$",
        url,
    )
    if m_pub:
        username = m_pub.group(1)
        msg_id = int(m_pub.group(2))
        return username, msg_id
    raise TelegramOperationError(
        "Link Telegram non riconosciuto. Usa un link a un messaggio nel formato https://t.me/..."
    )


def _extract_text(obj: Any) -> str:
    """Estrae il testo da un campo che puo' essere str o TextWithEntities."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if hasattr(obj, "text"):
        return obj.text or ""
    return str(obj)


def _normalize_chat_ref(chat_ref: Any) -> ChatRef:
    """Converte una rappresentazione serializzata (str o int) nel formato che Telethon accetta."""
    if isinstance(chat_ref, int):
        return chat_ref
    if isinstance(chat_ref, str):
        s = chat_ref.strip()
        # se sembra un intero negativo lo trattiamo come ID di canale
        if s.startswith("-") and s[1:].isdigit():
            return int(s)
        if s.isdigit():
            return int(s)
        return s.lstrip("@")
    raise TelegramOperationError(f"chat_ref non valido: {chat_ref!r}")


# ---------------------------------------------------------------------------
# Lettura segreti
# ---------------------------------------------------------------------------

def get_api_credentials() -> Tuple[int, str]:
    """Ritorna (api_id, api_hash) da st.secrets. Lancia TelegramConfigError se mancano."""
    try:
        api_id_raw = st.secrets["TELEGRAM_API_ID"]
        api_hash = st.secrets["TELEGRAM_API_HASH"]
    except (KeyError, FileNotFoundError) as e:
        raise TelegramConfigError(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH non configurati nei secrets Streamlit."
        ) from e
    try:
        api_id = int(api_id_raw)
    except (TypeError, ValueError) as e:
        raise TelegramConfigError("TELEGRAM_API_ID deve essere un intero.") from e
    return api_id, api_hash


def _get_fernet() -> Fernet:
    try:
        key = st.secrets["TELEGRAM_SESSION_FERNET_KEY"]
    except (KeyError, FileNotFoundError) as e:
        raise TelegramConfigError(
            "TELEGRAM_SESSION_FERNET_KEY non configurato nei secrets Streamlit."
        ) from e
    if isinstance(key, str):
        key = key.encode("utf-8")
    try:
        return Fernet(key)
    except Exception as e:
        raise TelegramConfigError("TELEGRAM_SESSION_FERNET_KEY non valido (deve essere una chiave Fernet base64 a 44 caratteri).") from e


def is_telegram_configured() -> bool:
    """True se i tre segreti minimi sono presenti, senza sollevare eccezioni."""
    try:
        get_api_credentials()
        _get_fernet()
        return True
    except TelegramConfigError:
        return False


# ---------------------------------------------------------------------------
# Encrypt / Decrypt StringSession
# ---------------------------------------------------------------------------

def encrypt_session(session_string: str) -> str:
    """Cifra una StringSession e restituisce il token Fernet (string)."""
    if not session_string:
        return ""
    return _get_fernet().encrypt(session_string.encode("utf-8")).decode("utf-8")


def decrypt_session(token: str) -> str:
    """Decifra una stringa salvata con encrypt_session(). Lancia InvalidToken se corrotta."""
    if not token:
        return ""
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def try_decrypt_session(token: str) -> Optional[str]:
    """Variante non-throwing: ritorna None se il token non e' decifrabile."""
    if not token:
        return None
    try:
        return decrypt_session(token)
    except (InvalidToken, TelegramConfigError):
        return None


# ---------------------------------------------------------------------------
# Asyncio helper
# ---------------------------------------------------------------------------

def _run(coro):
    """
    Esegue una coroutine in un nuovo event loop.

    Usato per chiamare Telethon (async) da Streamlit (sync). Ogni chiamata crea
    e chiude un proprio loop -> nessun rischio di state condiviso tra rerun.
    """
    try:
        return asyncio.run(coro)
    except RuntimeError as e:
        # Edge case: se Streamlit/altri stanno gia' girando un loop nel main thread,
        # asyncio.run alza "asyncio.run() cannot be called from a running event loop".
        if "running event loop" not in str(e):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Login wizard (programmatic flow)
#
# Lo state da persistere fra step nel session_state Streamlit:
#   - phone (str)
#   - phone_code_hash (str)
#   - intermediate_session (str)  # StringSession dopo send_code_request
#   - awaiting_2fa (bool)
# ---------------------------------------------------------------------------

# Identificatore "dispositivo" che l'organizer vedra' nelle sessioni attive del suo Telegram.
DEVICE_MODEL = "AV Assistant"
SYSTEM_VERSION = "Streamlit Cloud"
APP_VERSION = "1.0"


def _new_client(session_string: str = "") -> TelegramClient:
    api_id, api_hash = get_api_credentials()
    return TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
    )


async def _send_code_async(phone: str) -> Tuple[str, str]:
    """Step 1 del login: chiede a Telegram di inviare il codice al numero.

    Ritorna (intermediate_session_string, phone_code_hash).
    """
    client = _new_client("")
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        intermediate = client.session.save()
        return intermediate, sent.phone_code_hash
    finally:
        await client.disconnect()


def send_code(phone: str) -> Tuple[str, str]:
    """Sync wrapper di _send_code_async. Lancia TelegramLoginError per errori utente noti."""
    try:
        return _run(_send_code_async(phone))
    except PhoneNumberInvalidError as e:
        raise TelegramLoginError("Numero di telefono non valido. Usa il formato internazionale (es. +39...).") from e
    except FloodWaitError as e:
        raise TelegramLoginError(f"Troppi tentativi. Riprova fra {e.seconds} secondi.") from e


async def _sign_in_code_async(
    intermediate_session: str, phone: str, code: str, phone_code_hash: str
) -> Tuple[str, bool]:
    """Step 2: usa il codice ricevuto.

    Ritorna (next_session_string, needs_2fa). Se needs_2fa=True, il chiamante
    deve poi invocare sign_in_password() col token next_session_string.
    """
    client = _new_client(intermediate_session)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            return client.session.save(), False
        except SessionPasswordNeededError:
            # Il codice e' stato accettato ma serve la password 2FA.
            return client.session.save(), True
    finally:
        await client.disconnect()


def sign_in_with_code(
    intermediate_session: str, phone: str, code: str, phone_code_hash: str
) -> Tuple[str, bool]:
    try:
        return _run(_sign_in_code_async(intermediate_session, phone, code, phone_code_hash))
    except PhoneCodeInvalidError as e:
        raise TelegramLoginError("Codice non valido. Controlla e riprova.") from e
    except PhoneCodeExpiredError as e:
        raise TelegramLoginError("Codice scaduto. Richiedi un nuovo codice.") from e
    except FloodWaitError as e:
        raise TelegramLoginError(f"Troppi tentativi. Riprova fra {e.seconds} secondi.") from e


async def _sign_in_password_async(intermediate_session: str, password: str) -> str:
    client = _new_client(intermediate_session)
    await client.connect()
    try:
        await client.sign_in(password=password)
        return client.session.save()
    finally:
        await client.disconnect()


def sign_in_with_password(intermediate_session: str, password: str) -> str:
    try:
        return _run(_sign_in_password_async(intermediate_session, password))
    except FloodWaitError as e:
        raise TelegramLoginError(f"Troppi tentativi. Riprova fra {e.seconds} secondi.") from e
    except Exception as e:  # password sbagliata -> Telethon alza Exception generica
        # Identifichiamo l'errore di password sbagliata via stringa per non legarci a una classe interna.
        msg = str(e).lower()
        if "password" in msg:
            raise TelegramLoginError("Password 2FA errata.") from e
        raise


# ---------------------------------------------------------------------------
# Stato corrente / logout
# ---------------------------------------------------------------------------

async def _whoami_async(session_string: str) -> Optional[dict]:
    """Ritorna informazioni base sull'utente loggato, o None se la session non e' valida."""
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        me = await client.get_me()
        return {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone,
        }
    finally:
        await client.disconnect()


def whoami(session_string: str) -> Optional[dict]:
    try:
        return _run(_whoami_async(session_string))
    except Exception:
        # Una session revocata / corrotta alza errori vari; trattiamo come "non valido".
        return None


async def _logout_async(session_string: str) -> bool:
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False
        return await client.log_out()
    finally:
        await client.disconnect()


def logout(session_string: str) -> bool:
    """Revoca la sessione lato Telegram. Ritorna True se andato a buon fine."""
    try:
        return _run(_logout_async(session_string))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fase 2 - Lettura sondaggi
# ---------------------------------------------------------------------------

async def _resolve_username_async(session_string: str, username: str) -> Optional[dict]:
    if not username:
        return None
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        try:
            entity = await client.get_entity(username.lstrip("@"))
        except (UsernameInvalidError, UsernameNotOccupiedError, ValueError):
            return None
        return {
            "user_id": entity.id,
            "username": getattr(entity, "username", None),
            "first_name": getattr(entity, "first_name", "") or "",
            "last_name": getattr(entity, "last_name", "") or "",
        }
    finally:
        await client.disconnect()


def resolve_username(session_string: str, username: str) -> Optional[dict]:
    """Risolve un @username Telegram in {user_id, username, first_name, last_name}.

    Ritorna None se l'utente non e' raggiungibile o l'handle non esiste.
    """
    try:
        return _run(_resolve_username_async(session_string, username))
    except FloodWaitError as e:
        raise TelegramOperationError(f"Telegram rate limit, riprova fra {e.seconds}s.") from e
    except Exception:
        return None


async def _get_poll_message_async(
    session_string: str, chat_ref: ChatRef, msg_id: int
) -> dict:
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise TelegramOperationError("Sessione Telegram non valida. Riconnettiti.")
        try:
            entity = await client.get_entity(chat_ref)
        except (ValueError, UsernameNotOccupiedError) as e:
            raise TelegramOperationError(
                "Gruppo Telegram non trovato. Controlla di essere nel gruppo del link."
            ) from e
        except ChannelPrivateError as e:
            raise TelegramOperationError(
                "Gruppo privato a cui non hai accesso con questo account Telegram."
            ) from e
        msg = await client.get_messages(entity, ids=msg_id)
        if msg is None:
            raise TelegramOperationError("Messaggio non trovato in questo gruppo.")
        if not isinstance(msg.media, MessageMediaPoll):
            raise TelegramOperationError("Il messaggio linkato non e' un sondaggio.")
        poll = msg.media.poll
        options = []
        for i, ans in enumerate(poll.answers):
            options.append({
                "idx": i,
                "text": _extract_text(ans.text),
            })
        return {
            "poll_id": poll.id,
            "question": _extract_text(poll.question),
            "options": options,
            "is_anonymous": not getattr(poll, "public_voters", False),
            "is_closed": getattr(poll, "closed", False),
            "multiple_choice": getattr(poll, "multiple_choice", False),
        }
    finally:
        await client.disconnect()


def get_poll_message(session_string: str, chat_ref: ChatRef, msg_id: int) -> dict:
    """Ritorna metadata di un sondaggio Telegram.

    Output:
        {
          "poll_id": int,
          "question": str,
          "options": [{"idx": int, "text": str}, ...],
          "is_anonymous": bool,
          "is_closed": bool,
          "multiple_choice": bool,
        }

    Solleva TelegramOperationError per errori user-facing (gruppo non trovato,
    sondaggio anonimo, link sbagliato, ecc.).
    """
    chat_ref = _normalize_chat_ref(chat_ref)
    try:
        return _run(_get_poll_message_async(session_string, chat_ref, msg_id))
    except TelegramOperationError:
        raise
    except FloodWaitError as e:
        raise TelegramOperationError(f"Telegram rate limit, riprova fra {e.seconds}s.") from e
    except Exception as e:
        raise TelegramOperationError(f"Errore durante la lettura del sondaggio: {e}") from e


async def _get_poll_voters_async(
    session_string: str, chat_ref: ChatRef, msg_id: int, per_option_limit: int
) -> dict:
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise TelegramOperationError("Sessione Telegram non valida. Riconnettiti.")
        try:
            entity = await client.get_entity(chat_ref)
        except (ValueError, UsernameNotOccupiedError) as e:
            raise TelegramOperationError("Gruppo Telegram non trovato.") from e
        except ChannelPrivateError as e:
            raise TelegramOperationError("Gruppo privato non accessibile.") from e
        msg = await client.get_messages(entity, ids=msg_id)
        if msg is None or not isinstance(msg.media, MessageMediaPoll):
            raise TelegramOperationError("Sondaggio non trovato.")
        poll = msg.media.poll
        if not getattr(poll, "public_voters", False):
            raise TelegramOperationError(
                "Il sondaggio e' anonimo: Telegram non espone chi ha votato. "
                "L'organizer deve creare un sondaggio non-anonimo."
            )
        results_field = getattr(msg.media, "results", None)
        total_voters_unique = getattr(results_field, "total_voters", 0) if results_field else 0
        options_data: List[dict] = []
        for i, ans in enumerate(poll.answers):
            vot_res = await client(GetPollVotesRequest(
                peer=entity,
                id=msg_id,
                option=ans.option,
                limit=per_option_limit,
            ))
            voter_list = []
            for u in getattr(vot_res, "users", []) or []:
                voter_list.append({
                    "user_id": u.id,
                    "username": getattr(u, "username", None),
                    "first_name": getattr(u, "first_name", "") or "",
                    "last_name": getattr(u, "last_name", "") or "",
                })
            options_data.append({
                "idx": i,
                "text": _extract_text(ans.text),
                "voters": voter_list,
                "voter_count": len(voter_list),
            })
        return {
            "poll_id": poll.id,
            "question": _extract_text(poll.question),
            "is_closed": getattr(poll, "closed", False),
            "multiple_choice": getattr(poll, "multiple_choice", False),
            "total_voters_unique": total_voters_unique,
            "options": options_data,
        }
    finally:
        await client.disconnect()

def get_poll_voters(
    session_string: str, chat_ref: ChatRef, msg_id: int, per_option_limit: int = 200
) -> dict:
    """Ritorna l'elenco dei votanti per ogni opzione di un sondaggio non-anonimo.

    Output:
        {
          "poll_id": int,
          "question": str,
          "is_closed": bool,
          "multiple_choice": bool,
          "total_voters_unique": int,
          "options": [
            {
              "idx": int,
              "text": str,
              "voter_count": int,
              "voters": [{user_id, username, first_name, last_name}, ...],
            },
            ...
          ],
        }

    Solleva TelegramOperationError("POLL_VOTE_REQUIRED") se l'organizer non
    ha ancora votato nel sondaggio (vincolo Telegram).
    """
    chat_ref = _normalize_chat_ref(chat_ref)
    try:
        return _run(_get_poll_voters_async(session_string, chat_ref, msg_id, per_option_limit))
    except TelegramOperationError:
        raise
    except PollVoteRequiredError as e:
        raise TelegramOperationError(
            "Per leggere i votanti devi prima votare tu stesso nel sondaggio "
            "(vincolo di Telegram). Apri il sondaggio in Telegram, vota una "
            "qualunque opzione, poi torna qui e premi Aggiorna."
        ) from e
    except FloodWaitError as e:
        raise TelegramOperationError(f"Telegram rate limit, riprova fra {e.seconds}s.") from e
    except Exception as e:
        raise TelegramOperationError(f"Errore durante la lettura dei votanti: {e}") from e


# ---------------------------------------------------------------------------
# Fase 3 - Invio DM reminder
# ---------------------------------------------------------------------------

async def _send_dm_async(session_string: str, recipient: Any, text: str) -> dict:
    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"ok": False, "error": "session_invalid", "fallback_link": None}
        # Risolvi entity: recipient puo' essere user_id (int) o username (str senza @)
        try:
            entity = await client.get_entity(recipient)
        except (UsernameNotOccupiedError, UsernameInvalidError, ValueError):
            return {"ok": False, "error": "unknown_user", "fallback_link": None}
        uname = getattr(entity, "username", None)
        fb = f"https://t.me/{uname}" if uname else None
        try:
            await client.send_message(entity, text)
            return {
                "ok": True,
                "error": None,
                "user_id": entity.id,
                "username": uname,
                "fallback_link": fb,
            }
        except UserPrivacyRestrictedError:
            return {"ok": False, "error": "privacy", "user_id": entity.id, "username": uname, "fallback_link": fb}
        except UserIsBlockedError:
            return {"ok": False, "error": "blocked_by_user", "user_id": entity.id, "username": uname, "fallback_link": fb}
        except YouBlockedUserError:
            return {"ok": False, "error": "you_blocked_them", "user_id": entity.id, "username": uname, "fallback_link": fb}
        except InputUserDeactivatedError:
            return {"ok": False, "error": "deactivated", "user_id": entity.id, "username": uname, "fallback_link": None}
        except (PeerIdInvalidError, UserIdInvalidError):
            return {"ok": False, "error": "invalid_id", "fallback_link": fb}
    finally:
        await client.disconnect()


def send_dm(session_string: str, recipient: Any, text: str) -> dict:
    """Invia un DM testuale tramite l'account Telethon dell'organizer.

    recipient: int (user_id Telegram) oppure str (username senza @).
    text: testo del messaggio.

    Ritorno (dict):
      {
        "ok": bool,
        "error": str | None,   # "privacy" | "blocked_by_user" | "you_blocked_them"
                                  # | "deactivated" | "unknown_user" | "invalid_id"
                                  # | "session_invalid" | "unknown: <descr>"
        "user_id": int | omitted,
        "username": str | None,
        "fallback_link": str | None,  # https://t.me/<username> se disponibile
      }

    Solleva TelegramOperationError per errori che bloccano l'intero batch:
      - PeerFloodError (anti-spam Telegram: l'organizer e' stato flaggato).
      - FloodWaitError (rate-limit, riprovare fra N secondi).
    """
    try:
        return _run(_send_dm_async(session_string, recipient, text))
    except PeerFloodError as e:
        raise TelegramOperationError(
            "Telegram ha temporaneamente bloccato il tuo account dall'invio di DM verso "
            "non-contatti (protezione anti-spam). Attendi qualche ora prima di riprovare, "
            "oppure aggiungi i destinatari ai tuoi contatti Telegram."
        ) from e
    except FloodWaitError as e:
        raise TelegramOperationError(f"Rate limit Telegram: riprova fra {e.seconds}s.") from e
    except TelegramOperationError:
        raise
    except Exception as e:
        return {"ok": False, "error": f"unknown: {e}", "fallback_link": None}
