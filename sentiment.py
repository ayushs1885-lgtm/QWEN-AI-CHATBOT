"""
Multilingual sentiment/escalation module.

Classifies each message (in the context of conversation history) into
positive / neutral / negative / frustrated / urgent / sarcastic with
per-category confidence scores, derives a response-tone adjustment (never a
policy change), detects high-risk issues, tracks repeated-negative streaks,
routes urgent/after-hours complaints to the right queue, and auto-escalates
unresolved negative conversations after a configurable timeout.

Wire into an existing FastAPI app with:

    from sentiment import router as sentiment_router, start_sentiment_sweep
    app.include_router(sentiment_router)
    # in an on_event("startup") handler: start_sentiment_sweep()

Storage is in-memory — fine for a demo, resets on restart/redeploy.
"""

import os
import re
import json
import uuid
import asyncio
from datetime import datetime, timedelta, time as dtime
from typing import Optional

import httpx
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

CATEGORIES = ["positive", "neutral", "negative", "frustrated", "urgent", "sarcastic"]
NEGATIVE_LIKE = {"negative", "frustrated"}
HIGH_RISK_REASONS = {"account_compromise", "duplicate_payment", "legal_threat"}

# --- Runtime-configurable rules (changeable via /config endpoint, no redeploy) ---
CONFIG = {
    "business_days": [0, 1, 2, 3, 4],  # Mon=0 ... Sun=6
    "business_start": "09:00",
    "business_end": "18:00",
    "negative_streak_threshold": 3,       # consecutive negative-ish messages -> escalate
    "unresolved_negative_minutes": 15,    # unresolved negative conversation -> auto-escalate
    "negative_score_threshold": 0.5,      # score above which a message counts as "negative" for streak/timeout purposes
}

CONVERSATIONS: dict = {}   # conversation_id -> state
ESCALATIONS: list = []     # flat log of every escalation raised


# =========================================================================
# Business-hours helper (self-contained; supports a `now` override so tests
# can simulate specific times/dates without waiting for the real clock)
# =========================================================================

def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def is_business_moment(dt: datetime) -> bool:
    if dt.weekday() not in CONFIG["business_days"]:
        return False
    start = _parse_hhmm(CONFIG["business_start"])
    end = _parse_hhmm(CONFIG["business_end"])
    return start <= dt.time() < end


# =========================================================================
# PII masking for conversation summaries handed to escalation records
# =========================================================================

EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def mask_pii(text: str) -> str:
    if not text:
        return text
    text = CARD_RE.sub("[CARD-REDACTED]", text)
    text = EMAIL_RE.sub("[EMAIL-REDACTED]", text)
    text = PHONE_RE.sub("[PHONE-REDACTED]", text)
    return text


# =========================================================================
# LLM-backed classification: multilingual sentiment + high-risk detection
# =========================================================================

CLASSIFY_SYSTEM = """You are a multilingual customer-message classifier. The message may be in any
language. Consider the CURRENT message together with the CONVERSATION HISTORY provided for context
(e.g. sarcasm or urgency is often only clear from context, and repeated complaints matter).

Respond with ONLY JSON in this exact shape:
{
  "language": "detected language name",
  "scores": {
    "positive": 0.0, "neutral": 0.0, "negative": 0.0,
    "frustrated": 0.0, "urgent": 0.0, "sarcastic": 0.0
  },
  "primary_sentiment": one of ["positive","neutral","negative","frustrated","urgent","sarcastic"],
  "is_high_risk": true or false,
  "high_risk_reason": one of ["account_compromise","duplicate_payment","legal_threat"] or null,
  "tone_guidance": "one short sentence on HOW the reply's tone should adjust (e.g. calmer, more apologetic,
                    more formal, reassuring) — this must never suggest changing any policy, discount,
                    refund amount, or business rule, only tone/wording"
}
Scores are independent 0-1 confidences (a message can be both "negative" and "sarcastic" at once,
or calmly worded yet still high-risk — e.g. a calm message reporting a compromised account is still
high-risk even though its sentiment may score as neutral)."""


async def classify_message(message: str, history: list) -> Optional[dict]:
    if not GROQ_API_KEY:
        return None
    history_text = "\n".join(f"{h['role']}: {h['message']}" for h in history[-6:])
    user_content = f"CONVERSATION HISTORY:\n{history_text}\n\nCURRENT MESSAGE:\n{message}"
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
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
        parsed = json.loads(content.strip())
        for cat in CATEGORIES:
            parsed.setdefault("scores", {}).setdefault(cat, 0.0)
        return parsed
    except Exception:
        return None


# =========================================================================
# Conversation state helpers
# =========================================================================

def _get_or_create_conversation(conversation_id: Optional[str]) -> tuple:
    if conversation_id and conversation_id in CONVERSATIONS:
        return conversation_id, CONVERSATIONS[conversation_id]
    new_id = conversation_id or str(uuid.uuid4())[:8]
    CONVERSATIONS[new_id] = {
        "history": [],
        "negative_streak": 0,
        "first_unresolved_negative_at": None,
        "resolved": True,
        "escalated_reasons": set(),
    }
    return new_id, CONVERSATIONS[new_id]


def _summarize_conversation(convo: dict, latest_message: str) -> str:
    recent = convo["history"][-5:]
    lines = [f"{h['role']}: {mask_pii(h['message'])}" for h in recent]
    lines.append(f"user: {mask_pii(latest_message)}")
    return " | ".join(lines)


def _raise_escalation(convo_id: str, convo: dict, reason: str, condition: str, latest_message: str):
    record = {
        "id": str(uuid.uuid4())[:8],
        "conversation_id": convo_id,
        "reason": reason,
        "activated_condition": condition,
        "conversation_summary": _summarize_conversation(convo, latest_message),
        "timestamp": datetime.utcnow().isoformat(),
    }
    ESCALATIONS.append(record)
    convo["escalated_reasons"].add(condition)
    return record


# =========================================================================
# Main endpoint
# =========================================================================

@router.post("/analyze")
async def analyze_message(payload: dict = Body(...)):
    """
    Body:
    {
      "conversation_id": "abc123",   // optional — omit to start a new conversation
      "message": "...",
      "now": "2026-09-07T22:30:00"   // optional ISO override, for simulating a specific time
    }
    """
    message = payload.get("message", "").strip()
    if not message:
        return {"error": "message is required"}

    now = datetime.fromisoformat(payload["now"]) if payload.get("now") else datetime.utcnow()
    convo_id, convo = _get_or_create_conversation(payload.get("conversation_id"))

    classification = await classify_message(message, convo["history"])
    if not classification:
        return {"error": "Could not classify message. Check GROQ_API_KEY."}

    scores = classification["scores"]
    primary = classification.get("primary_sentiment", "neutral")
    is_high_risk = bool(classification.get("is_high_risk"))
    high_risk_reason = classification.get("high_risk_reason")
    tone_guidance = classification.get("tone_guidance", "")

    is_negative_ish = scores.get("negative", 0) >= CONFIG["negative_score_threshold"] or \
        scores.get("frustrated", 0) >= CONFIG["negative_score_threshold"]
    is_urgent = scores.get("urgent", 0) >= CONFIG["negative_score_threshold"] or primary == "urgent"

    convo["history"].append({"role": "user", "message": message, "timestamp": now.isoformat(), "classification": classification})

    # --- streak tracking ---
    if is_negative_ish:
        convo["negative_streak"] += 1
        if convo["first_unresolved_negative_at"] is None:
            convo["first_unresolved_negative_at"] = now
        convo["resolved"] = False
    else:
        convo["negative_streak"] = 0
        # a clearly positive/neutral turn is treated as the issue being addressed
        if primary in ("positive", "neutral"):
            convo["first_unresolved_negative_at"] = None
            convo["resolved"] = True

    triggered_escalations = []

    # --- rule 1: high-risk issues escalate immediately, regardless of tone ---
    if is_high_risk and high_risk_reason in HIGH_RISK_REASONS:
        rec = _raise_escalation(convo_id, convo, reason=f"high_risk:{high_risk_reason}",
                                 condition="high_risk_issue_detected", latest_message=message)
        triggered_escalations.append(rec)

    # --- rule 2: repeated negative messages escalate ---
    if convo["negative_streak"] >= CONFIG["negative_streak_threshold"] and \
       "repeated_negative_streak" not in convo["escalated_reasons"]:
        rec = _raise_escalation(convo_id, convo, reason="repeated_negative_messages",
                                 condition="repeated_negative_streak", latest_message=message)
        triggered_escalations.append(rec)

    # --- rule 3: urgent-after-hours -> on-call queue; normal complaint -> next business day ---
    queue = None
    if is_urgent or is_negative_ish:
        after_hours = not is_business_moment(now)
        if is_urgent and after_hours:
            queue = "on_call"
        elif after_hours:
            queue = "next_business_day"
        else:
            queue = "immediate"

    return {
        "conversation_id": convo_id,
        "classification": classification,
        "is_negative_like": is_negative_ish,
        "negative_streak": convo["negative_streak"],
        "queue": queue,
        "after_hours": not is_business_moment(now),
        "escalations_triggered": triggered_escalations,
        "tone_guidance": tone_guidance,
        "note": "tone_guidance adjusts wording/empathy only — it must not be used to alter policy, "
                "pricing, refund amounts, or any business rule.",
    }


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str):
    convo = CONVERSATIONS.get(conversation_id)
    if not convo:
        return {"error": "not found"}
    convo["resolved"] = True
    convo["first_unresolved_negative_at"] = None
    convo["negative_streak"] = 0
    return {"resolved": True}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    convo = CONVERSATIONS.get(conversation_id)
    if not convo:
        return {"error": "not found"}
    out = dict(convo)
    out["escalated_reasons"] = list(convo["escalated_reasons"])
    return out


@router.get("/escalations")
async def list_escalations():
    return {"count": len(ESCALATIONS), "escalations": ESCALATIONS}


@router.get("/config")
async def get_config():
    return CONFIG


@router.post("/config")
async def update_config(payload: dict = Body(...)):
    """Runtime update of business hours / thresholds — no redeploy needed."""
    for key in ["business_days", "business_start", "business_end",
                "negative_streak_threshold", "unresolved_negative_minutes",
                "negative_score_threshold"]:
        if key in payload:
            CONFIG[key] = payload[key]
    return {"updated": True, "config": CONFIG}


# =========================================================================
# Background sweep: auto-escalate negative conversations unresolved for
# longer than the configured timeout.
# =========================================================================

async def _sweep_loop():
    while True:
        now = datetime.utcnow()
        for convo_id, convo in CONVERSATIONS.items():
            started = convo.get("first_unresolved_negative_at")
            if started and not convo["resolved"] and "unresolved_negative_timeout" not in convo["escalated_reasons"]:
                elapsed_minutes = (now - started).total_seconds() / 60.0
                if elapsed_minutes >= CONFIG["unresolved_negative_minutes"]:
                    last_message = convo["history"][-1]["message"] if convo["history"] else ""
                    _raise_escalation(convo_id, convo, reason="unresolved_negative_timeout",
                                       condition="unresolved_negative_timeout", latest_message=last_message)
        await asyncio.sleep(30)


def start_sentiment_sweep():
    asyncio.create_task(_sweep_loop())