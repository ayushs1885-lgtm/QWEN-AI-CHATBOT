"""
Multilingual session module.

Handles multi-turn conversations across languages (including mixed-language
and mid-conversation language switching), preserves key entities (names,
order IDs, dates, product codes) across turns regardless of language,
retains at least 10 messages of context, isolates concurrent customer
sessions, asks for clarification on low language/intent confidence, copes
with spelling errors/transliteration/multi-intent messages/corrections, and
manages session lifecycle: 30-minute inactivity timeout, with a 24-hour
"return window" that restores a summary instead of starting cold.

Wire into an existing FastAPI app with:

    from session import router as session_router, start_session_sweep
    app.include_router(session_router)
    # in an on_event("startup") handler: start_session_sweep()

Storage is in-memory — fine for a demo, resets on restart/redeploy.
"""

import os
import re
import json
import uuid
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/session", tags=["session"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

# --- Runtime-configurable rules (no redeploy needed to change these) ---
CONFIG = {
    "supported_languages": ["English", "Hindi", "Spanish", "Arabic"],  # English + 3 more, adjustable
    "language_confidence_threshold": 0.55,
    "intent_confidence_threshold": 0.55,
    "session_timeout_minutes": 30,
    "return_window_hours": 24,
    "history_limit": 10,
}

SESSIONS: dict = {}   # session_id -> state


def _new_session_state() -> dict:
    now = datetime.utcnow()
    return {
        "history": [],
        "entities": {"names": [], "order_ids": [], "dates": [], "product_codes": []},
        "created_at": now,
        "last_activity": now,
        "status": "active",       # active | expired
        "summary": None,
        "primary_language": None,
    }


def _merge_entities(existing: dict, new: dict, corrections: dict):
    """Adds newly-seen entities and applies explicit corrections (a
    correction replaces the prior value for that entity type rather than
    just appending a new one alongside it)."""
    for field in ["names", "order_ids", "dates", "product_codes"]:
        if field in corrections and corrections[field]:
            existing[field] = [corrections[field]]
            continue
        for val in new.get(field, []) or []:
            if val and val not in existing[field]:
                existing[field].append(val)


def _local_summary(state: dict) -> str:
    """Lightweight, no-extra-API-call summary used when a session expires,
    so it's ready instantly if the customer returns within the window."""
    ent = state["entities"]
    ent_parts = []
    if ent["names"]:
        ent_parts.append(f"customer: {', '.join(ent['names'])}")
    if ent["order_ids"]:
        ent_parts.append(f"order(s): {', '.join(ent['order_ids'])}")
    if ent["product_codes"]:
        ent_parts.append(f"product(s): {', '.join(ent['product_codes'])}")
    if ent["dates"]:
        ent_parts.append(f"date(s) mentioned: {', '.join(ent['dates'])}")
    last_msgs = state["history"][-3:]
    last_text = " | ".join(f"{h['role']}: {h['message']}" for h in last_msgs)
    ent_str = "; ".join(ent_parts) if ent_parts else "no structured details captured"
    return f"[{ent_str}] Last exchange: {last_text}"


# =========================================================================
# LLM-backed language/intent classification + entity extraction
# =========================================================================

CLASSIFY_SYSTEM = f"""You are a multilingual customer-support message analyzer. The customer's
message may be in any language, a mix of languages in one sentence, transliterated (e.g. a
non-Latin-script language typed in Latin letters), misspelled, or informal. The officially
supported languages for this assistant are: {', '.join(CONFIG['supported_languages'])} — but
still analyze any language the customer actually uses.

Given the CURRENT message and the CONVERSATION HISTORY (for context, entity carry-over, and
detecting corrections), respond with ONLY JSON in this exact shape:
{{
  "detected_languages": ["..."],          // all languages present, even if mixed in one message
  "primary_language": "...",              // the dominant one to reply in
  "language_confidence": 0.0,
  "intent_confidence": 0.0,               // how clearly the customer's request/intent is understood
  "entities": {{
    "names": [], "order_ids": [], "dates": [], "product_codes": []
  }},                                       // NEW entities mentioned in the current message only
  "corrections": {{
    "names": null, "order_ids": null, "dates": null, "product_codes": null
  }},                                       // set a field ONLY if the customer is explicitly correcting
                                             // a previously given value (e.g. "actually my order id is X, not Y")
  "multiple_requests": [],                 // if the message contains more than one distinct ask, list each briefly
  "needs_clarification": false,
  "clarification_question": null           // if language or intent confidence is low, a short clarifying
                                             // question phrased in the customer's own detected language
}}
Correct for likely spelling errors and transliteration when interpreting intent — do not let typos
alone lower intent_confidence if the meaning is still reasonably clear."""


async def classify(message: str, history: list) -> Optional[dict]:
    if not GROQ_API_KEY:
        return None
    history_text = "\n".join(f"{h['role']} ({h.get('language','?')}): {h['message']}" for h in history[-10:])
    user_content = f"CONVERSATION HISTORY:\n{history_text}\n\nCURRENT MESSAGE:\n{message}"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(GROQ_API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
        return json.loads(content.strip())
    except Exception:
        return None


REPLY_SYSTEM_TEMPLATE = """You are a helpful, natural-sounding multilingual customer support
assistant. Reply primarily in {language}. If the customer mixed in another language, it's fine
to mirror a little of that mixing back naturally, the way a bilingual support agent would.
Use the persisted customer details below where relevant instead of asking for them again:
{entities}
Address every distinct request in the customer's message if there is more than one. Keep the
tone warm and professional."""


async def generate_reply(message: str, language: str, entities: dict, history: list,
                          temperature: float = 0.7, max_tokens: int = 700) -> str:
    if not GROQ_API_KEY:
        return "GROQ_API_KEY is not set."
    entities_str = json.dumps({k: v for k, v in entities.items() if v}) or "none captured yet"
    system = REPLY_SYSTEM_TEMPLATE.format(language=language or "the customer's language", entities=entities_str)
    history_text = "\n".join(f"{h['role']}: {h['message']}" for h in history[-10:])
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Recent conversation:\n{history_text}\n\nCustomer's latest message:\n{message}"},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(GROQ_API_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            return f"Error from Groq API: status {resp.status_code}"
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Groq API is unreachable: {e}"


# =========================================================================
# Session lifecycle
# =========================================================================

def _handle_lifecycle(session_id: str, now: datetime) -> tuple:
    """Returns (state, restored: bool, is_new: bool). Applies the
    30-minute-timeout / 24-hour-restore-window / fresh-session rules."""
    state = SESSIONS.get(session_id)

    if state is None:
        SESSIONS[session_id] = _new_session_state()
        return SESSIONS[session_id], False, True

    elapsed_since_activity = now - state["last_activity"]
    timeout = timedelta(minutes=CONFIG["session_timeout_minutes"])
    return_window = timedelta(hours=CONFIG["return_window_hours"])

    if state["status"] == "active" and elapsed_since_activity > timeout:
        state["status"] = "expired"
        if not state["summary"]:
            state["summary"] = _local_summary(state)

    if state["status"] == "expired":
        if elapsed_since_activity <= return_window:
            # Returning within the window: restore summary + entities into a
            # freshly-active session rather than starting cold.
            restored_summary = state["summary"]
            new_state = _new_session_state()
            new_state["entities"] = state["entities"]
            new_state["history"] = [{"role": "system", "message": f"[Restored session summary: {restored_summary}]",
                                      "language": "system", "timestamp": now.isoformat()}]
            SESSIONS[session_id] = new_state
            return new_state, True, False
        else:
            # Past the return window: begin a completely new session.
            SESSIONS[session_id] = _new_session_state()
            return SESSIONS[session_id], False, True

    return state, False, False


# =========================================================================
# Main endpoint
# =========================================================================

@router.post("/message")
async def handle_message(payload: dict = Body(...)):
    """
    Body:
    {
      "session_id": "abc123",     // omit to start a brand-new session
      "message": "...",
      "now": "2026-09-07T22:00:00" // optional ISO override, for simulating timeouts/returns
    }
    """
    message = payload.get("message", "").strip()
    if not message:
        return {"error": "message is required"}

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else datetime.utcnow()
    session_id = payload.get("session_id") or str(uuid.uuid4())[:8]

    state, restored, is_new = _handle_lifecycle(session_id, now)

    classification = await classify(message, state["history"])
    if not classification:
        return {"error": "Could not classify message. Check GROQ_API_KEY.", "session_id": session_id}

    lang_conf = classification.get("language_confidence", 0)
    intent_conf = classification.get("intent_confidence", 0)
    primary_language = classification.get("primary_language", "English")
    needs_clarification = bool(classification.get("needs_clarification")) or \
        lang_conf < CONFIG["language_confidence_threshold"] or \
        intent_conf < CONFIG["intent_confidence_threshold"]

    _merge_entities(state["entities"], classification.get("entities", {}), classification.get("corrections", {}) or {})
    state["primary_language"] = primary_language

    state["history"].append({"role": "user", "message": message, "language": primary_language, "timestamp": now.isoformat()})

    if needs_clarification:
        reply = classification.get("clarification_question") or \
            "Could you clarify what you'd like help with?"
    else:
        reply = await generate_reply(message, primary_language, state["entities"], state["history"])

    state["history"].append({"role": "assistant", "message": reply, "language": primary_language, "timestamp": now.isoformat()})
    # keep only the configured window of turns as *active* context (older
    # turns are dropped, not the whole session — this satisfies "retain
    # context for at least N messages" without unbounded memory growth)
    state["history"] = state["history"][-(CONFIG["history_limit"] * 2):]
    state["last_activity"] = now

    return {
        "session_id": session_id,
        "session_status": state["status"],
        "restored_from_previous_session": restored,
        "is_new_session": is_new,
        "detected_languages": classification.get("detected_languages", [primary_language]),
        "primary_language": primary_language,
        "language_confidence": lang_conf,
        "intent_confidence": intent_conf,
        "needs_clarification": needs_clarification,
        "multiple_requests_detected": classification.get("multiple_requests", []),
        "entities": state["entities"],
        "reply": reply,
        "history_length": len(state["history"]),
    }


@router.get("/{session_id}")
async def get_session(session_id: str):
    state = SESSIONS.get(session_id)
    if not state:
        return {"error": "not found"}
    out = dict(state)
    out["created_at"] = state["created_at"].isoformat()
    out["last_activity"] = state["last_activity"].isoformat()
    return out


@router.get("/config")
async def get_config():
    return CONFIG


@router.post("/config")
async def update_config(payload: dict = Body(...)):
    """Runtime update of supported languages / thresholds / timings — no redeploy needed."""
    for key in ["supported_languages", "language_confidence_threshold", "intent_confidence_threshold",
                "session_timeout_minutes", "return_window_hours", "history_limit"]:
        if key in payload:
            CONFIG[key] = payload[key]
    return {"updated": True, "config": CONFIG}


# =========================================================================
# Background sweep: expire idle sessions (so a returning customer sees an
# already-prepared summary) and purge sessions past the return window.
# =========================================================================

async def _sweep_loop():
    while True:
        now = datetime.utcnow()
        timeout = timedelta(minutes=CONFIG["session_timeout_minutes"])
        return_window = timedelta(hours=CONFIG["return_window_hours"])
        to_delete = []
        for sid, state in SESSIONS.items():
            elapsed = now - state["last_activity"]
            if state["status"] == "active" and elapsed > timeout:
                state["status"] = "expired"
                if not state["summary"]:
                    state["summary"] = _local_summary(state)
            if state["status"] == "expired" and elapsed > return_window:
                to_delete.append(sid)
        for sid in to_delete:
            del SESSIONS[sid]
        await asyncio.sleep(60)


def start_session_sweep():
    asyncio.create_task(_sweep_loop())