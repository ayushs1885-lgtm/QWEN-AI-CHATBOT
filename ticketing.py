"""
Support ticket automation module.

Converts unresolved conversation transcripts into structured, routed,
SLA-tracked support tickets. Designed as a standalone APIRouter so it can be
wired into an existing FastAPI app with two lines:

    from ticketing import router as ticketing_router
    app.include_router(ticketing_router)

Storage is in-memory (a dict) — fine for a demo/small deployment, but resets
whenever the server restarts or redeploys. For anything persistent, swap
TICKETS/CASES for a real database.
"""

import os
import re
import json
import uuid
import hashlib
import asyncio
from datetime import datetime, timedelta, date, time as dtime
from typing import Optional

import httpx
from fastapi import APIRouter, Body

router = APIRouter(prefix="/api", tags=["ticketing"])

# --- Groq config (kept self-contained so this file has no import dependency
#     on main.py — avoids circular imports). Reads the same env vars. ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")


# =========================================================================
# Runtime-configurable business rules
# =========================================================================
# Everything here can be changed WITHOUT a redeploy via the /api/config/*
# endpoints below — this is what lets SLA rules, business hours, holidays,
# and team availability change at runtime.

CONFIG = {
    "business_days": [0, 1, 2, 3, 4],  # Mon=0 ... Sun=6; default Mon-Fri
    "business_start": "09:00",
    "business_end": "18:00",
    "holidays": [],  # list of "YYYY-MM-DD" strings
    "sla_hours": {  # business hours allowed per priority before breach
        "P1": 4,
        "P2": 8,
        "P3": 24,
        "P4": 48,
    },
    "warning_threshold": 0.75,  # fraction of SLA consumed that triggers a warning
    "duplicate_window_hours": 24,
}

TEAMS = {
    "billing": {
        "skills": ["billing", "payment", "refund", "invoice"],
        "agents": [
            {"id": "b1", "name": "Agent B1", "available": True, "workload": 0, "capacity": 5},
            {"id": "b2", "name": "Agent B2", "available": True, "workload": 0, "capacity": 5},
        ],
    },
    "technical": {
        "skills": ["technical", "bug", "error", "login", "app_crash"],
        "agents": [
            {"id": "t1", "name": "Agent T1", "available": True, "workload": 0, "capacity": 5},
            {"id": "t2", "name": "Agent T2", "available": True, "workload": 0, "capacity": 5},
        ],
    },
    "shipping": {
        "skills": ["shipping", "delivery", "order_status", "tracking"],
        "agents": [
            {"id": "s1", "name": "Agent S1", "available": True, "workload": 0, "capacity": 5},
        ],
    },
    "general": {
        "skills": ["other", "account", "general"],
        "agents": [
            {"id": "g1", "name": "Agent G1", "available": True, "workload": 0, "capacity": 5},
        ],
    },
    "escalations": {
        "skills": ["escalation"],
        "agents": [
            {"id": "esc1", "name": "Escalation Manager", "available": True, "workload": 0, "capacity": 20},
        ],
    },
}

MANDATORY_FIELDS = ["customer_name", "contact", "order_id", "issue_summary"]

# In-memory stores
TICKETS: dict = {}   # ticket_id -> ticket dict
CASES: dict = {}      # case_id -> list of ticket_ids (grouping)


# =========================================================================
# Business-hours-aware time math
# =========================================================================

def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _is_business_day(d: date) -> bool:
    if d.weekday() not in CONFIG["business_days"]:
        return False
    if d.isoformat() in CONFIG["holidays"]:
        return False
    return True


def _is_business_moment(dt: datetime) -> bool:
    if not _is_business_day(dt.date()):
        return False
    start = _parse_hhmm(CONFIG["business_start"])
    end = _parse_hhmm(CONFIG["business_end"])
    return start <= dt.time() < end


def _next_business_start(dt: datetime) -> datetime:
    """If dt falls outside business hours, jump forward to the next moment
    business is open. If dt is already in business hours, returns dt."""
    start = _parse_hhmm(CONFIG["business_start"])
    end = _parse_hhmm(CONFIG["business_end"])
    cur = dt
    for _ in range(0, 3660):  # hard cap so a misconfigured calendar can't infinite-loop
        if _is_business_day(cur.date()):
            if cur.time() < start:
                return cur.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if cur.time() < end:
                return cur
        # move to next day's opening
        cur = (cur + timedelta(days=1)).replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return cur


def add_business_hours(start_dt: datetime, hours: float) -> datetime:
    """Returns the deadline datetime after adding `hours` of BUSINESS time to
    start_dt, skipping weekends/holidays and hours outside the configured
    business window."""
    remaining = timedelta(hours=hours)
    cur = _next_business_start(start_dt)
    end_hhmm = _parse_hhmm(CONFIG["business_end"])
    for _ in range(0, 3660):
        if remaining <= timedelta(0):
            return cur
        day_end = cur.replace(hour=end_hhmm.hour, minute=end_hhmm.minute, second=0, microsecond=0)
        available_today = day_end - cur
        if available_today <= timedelta(0):
            cur = _next_business_start(cur + timedelta(days=1))
            continue
        if remaining <= available_today:
            return cur + remaining
        remaining -= available_today
        cur = _next_business_start(cur + timedelta(days=1))
    return cur


def elapsed_business_hours(start_dt: datetime, end_dt: datetime) -> float:
    """Business hours elapsed between two datetimes (used to compute %SLA consumed)."""
    if end_dt <= start_dt:
        return 0.0
    total = timedelta(0)
    cur = _next_business_start(start_dt)
    end_hhmm = _parse_hhmm(CONFIG["business_end"])
    for _ in range(0, 3660):
        if cur >= end_dt:
            break
        day_end = cur.replace(hour=end_hhmm.hour, minute=end_hhmm.minute, second=0, microsecond=0)
        segment_end = min(day_end, end_dt)
        if segment_end > cur:
            total += segment_end - cur
        cur = _next_business_start(cur + timedelta(days=1))
    return total.total_seconds() / 3600.0


# =========================================================================
# PII masking (for the handoff summary — never expose raw contact/payment info)
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


def mask_name(name: str) -> str:
    if not name:
        return name
    parts = name.split()
    return " ".join(p[0] + "***" if len(p) > 1 else p for p in parts)


# =========================================================================
# LLM-backed extraction: customer / order / product / issue / evidence /
# contact, plus sentiment, severity, customer impact, and multi-issue split.
# =========================================================================

EXTRACTION_SYSTEM = """You convert a customer support conversation into structured JSON.
Extract ONLY what is explicitly present in the conversation. Never invent or guess
missing values — use null for anything not stated.

Respond with ONLY a single JSON object, no prose, no markdown fences, in this exact shape:
{
  "issues": [
    {
      "customer_name": string or null,
      "contact": string or null,           // email or phone, whichever is present
      "order_id": string or null,
      "product": string or null,
      "issue_summary": string or null,      // one-line description of this specific issue
      "issue_category": one of ["billing","technical","shipping","account","other"],
      "evidence": [string],                 // e.g. "screenshot mentioned", "error code E402", "order confirmation"
      "sentiment": one of ["positive","neutral","negative","angry"],
      "severity": one of ["low","medium","high","critical"],
      "customer_impact": one of ["single_user","multiple_users","business_critical"]
    }
  ]
}
If the conversation raises more than one UNRELATED problem (e.g. a billing complaint AND a
separate shipping complaint), list each as its own object in "issues" so they can be handled
as separate tickets. If multiple messages describe the SAME underlying problem, combine them
into a single issue object instead of duplicating it."""


async def call_groq_json(prompt: str) -> Optional[dict]:
    if not GROQ_API_KEY:
        return None
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
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


def missing_mandatory_fields(issue: dict) -> list:
    return [f for f in MANDATORY_FIELDS if not issue.get(f)]


# =========================================================================
# Priority scoring
# =========================================================================

SEVERITY_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}
SENTIMENT_SCORE = {"positive": 0, "neutral": 1, "negative": 2, "angry": 3}
IMPACT_SCORE = {"single_user": 1, "multiple_users": 2, "business_critical": 4}


def calculate_priority(issue: dict, waiting_hours: float) -> str:
    """Weighted combination of severity, sentiment, wait time, and customer
    impact -> P1 (most urgent) .. P4 (least urgent)."""
    severity = SEVERITY_SCORE.get(issue.get("severity", "medium"), 2)
    sentiment = SENTIMENT_SCORE.get(issue.get("sentiment", "neutral"), 1)
    impact = IMPACT_SCORE.get(issue.get("customer_impact", "single_user"), 1)
    wait_score = min(waiting_hours / 4.0, 3)  # every 4h waited adds a point, capped at 3

    score = (severity * 2) + (sentiment * 1.5) + (impact * 2) + wait_score

    if score >= 14 or issue.get("customer_impact") == "business_critical":
        return "P1"
    if score >= 10:
        return "P2"
    if score >= 6:
        return "P3"
    return "P4"


# =========================================================================
# Duplicate detection + grouping
# =========================================================================

def _signature(issue: dict) -> str:
    raw = f"{(issue.get('contact') or '').lower()}|{issue.get('order_id') or ''}|{issue.get('issue_category') or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def find_duplicate(issue: dict, now: datetime) -> Optional[str]:
    sig = _signature(issue)
    window = timedelta(hours=CONFIG["duplicate_window_hours"])
    for tid, t in TICKETS.items():
        if t.get("signature") == sig and (now - t["created_at"]) <= window and t["status"] != "closed":
            return tid
    return None


# =========================================================================
# Routing
# =========================================================================

CATEGORY_TEAM = {
    "billing": "billing",
    "technical": "technical",
    "shipping": "shipping",
    "account": "general",
    "other": "general",
}


def route_ticket(issue: dict, priority: str, now: datetime) -> dict:
    team_name = CATEGORY_TEAM.get(issue.get("issue_category", "other"), "general")
    team = TEAMS.get(team_name, TEAMS["general"])

    after_hours = not _is_business_moment(now)

    # pick the least-loaded available agent under capacity
    candidates = [a for a in team["agents"] if a["available"] and a["workload"] < a["capacity"]]
    candidates.sort(key=lambda a: a["workload"])

    if candidates:
        agent = candidates[0]
        agent["workload"] += 1
        return {
            "team": team_name,
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "team_unavailable": False,
            "after_hours": after_hours,
        }

    # team has no available/free-capacity agent -> escalate to escalations queue,
    # but only auto-assign there for P1/P2; lower priority just queues under the team
    if priority in ("P1", "P2"):
        esc = TEAMS["escalations"]["agents"][0]
        esc["workload"] += 1
        return {
            "team": team_name,
            "agent_id": esc["id"],
            "agent_name": esc["name"],
            "team_unavailable": True,
            "escalated_on_create": True,
            "after_hours": after_hours,
        }

    return {
        "team": team_name,
        "agent_id": None,
        "agent_name": None,
        "team_unavailable": True,
        "after_hours": after_hours,
    }


# =========================================================================
# SLA lifecycle: warning at threshold, auto-escalate on breach
# =========================================================================

def refresh_sla_status(ticket: dict, now: Optional[datetime] = None) -> dict:
    if ticket["status"] in ("closed", "escalated_breach"):
        return ticket
    now = now or datetime.utcnow()
    elapsed = elapsed_business_hours(ticket["created_at"], now)
    allowed = CONFIG["sla_hours"].get(ticket["priority"], 24)
    pct = elapsed / allowed if allowed else 1.0

    ticket["sla_elapsed_hours"] = round(elapsed, 2)
    ticket["sla_allowed_hours"] = allowed
    ticket["sla_pct_consumed"] = round(min(pct, 1.5), 3)

    if pct >= 1.0 and ticket["status"] != "escalated_breach":
        ticket["status"] = "escalated_breach"
        ticket["escalated_at"] = now.isoformat()
        esc = TEAMS["escalations"]["agents"][0]
        esc["workload"] += 1
        ticket["routing"]["escalated_to"] = esc["id"]
    elif pct >= CONFIG["warning_threshold"] and ticket["status"] == "open":
        ticket["status"] = "warning"
    return ticket


async def _sla_sweep_loop():
    """Background loop so SLA warnings/breaches get flagged even without new
    requests arriving. Note: on Render's free tier the service can spin down
    after inactivity, so this loop only runs while the instance is awake —
    it is not a substitute for a real scheduled job on always-on hosting."""
    while True:
        now = datetime.utcnow()
        for t in TICKETS.values():
            refresh_sla_status(t, now)
        await asyncio.sleep(60)


def start_background_sweep():
    asyncio.create_task(_sla_sweep_loop())


# =========================================================================
# Ticket creation
# =========================================================================

def _new_ticket(issue: dict, priority: str, routing: dict, now: datetime, case_id: str) -> dict:
    tid = str(uuid.uuid4())[:8]
    deadline = add_business_hours(now, CONFIG["sla_hours"].get(priority, 24))
    ticket = {
        "id": tid,
        "case_id": case_id,
        "created_at": now,
        "priority": priority,
        "status": "open",
        "signature": _signature(issue),
        "issue": issue,
        "missing_fields": missing_mandatory_fields(issue),
        "routing": routing,
        "sla_deadline": deadline.isoformat(),
        "sla_elapsed_hours": 0.0,
        "sla_allowed_hours": CONFIG["sla_hours"].get(priority, 24),
        "sla_pct_consumed": 0.0,
    }
    TICKETS[tid] = ticket
    return ticket


def _masked_handoff(ticket: dict) -> dict:
    issue = ticket["issue"]
    return {
        "ticket_id": ticket["id"],
        "priority": ticket["priority"],
        "status": ticket["status"],
        "team": ticket["routing"].get("team"),
        "agent": ticket["routing"].get("agent_name"),
        "customer_name_masked": mask_name(issue.get("customer_name") or ""),
        "contact_masked": mask_pii(issue.get("contact") or ""),
        "order_id": issue.get("order_id"),
        "product": issue.get("product"),
        "issue_summary": mask_pii(issue.get("issue_summary") or ""),
        "evidence": issue.get("evidence", []),
        "sentiment": issue.get("sentiment"),
        "severity": issue.get("severity"),
        "sla_deadline": ticket["sla_deadline"],
        "sla_pct_consumed": ticket["sla_pct_consumed"],
        "missing_fields": ticket["missing_fields"],
    }


# =========================================================================
# API endpoints
# =========================================================================

@router.post("/tickets/from-conversation")
async def create_tickets_from_conversation(payload: dict = Body(...)):
    """
    Body: {
      "conversation": "full transcript text",
      "waiting_hours": 0,        # optional — how long the customer has waited so far
      "created_at": "ISO8601"    # optional — defaults to now (useful for testing after-hours/backdated cases)
    }
    """
    conversation = payload.get("conversation", "")
    waiting_hours = float(payload.get("waiting_hours", 0))
    now = datetime.fromisoformat(payload["created_at"]) if payload.get("created_at") else datetime.utcnow()

    if not conversation.strip():
        return {"error": "conversation text is required"}

    extracted = await call_groq_json(conversation)
    if not extracted or "issues" not in extracted:
        return {"error": "Could not extract structured data from this conversation. Try again or check GROQ_API_KEY."}

    issues = extracted["issues"] or [{}]
    case_id = str(uuid.uuid4())[:8]
    results = []

    for issue in issues:
        dup_id = find_duplicate(issue, now)
        if dup_id:
            results.append({
                "duplicate_of": dup_id,
                "note": "This matches an existing open ticket for the same contact/order/category within the duplicate window.",
            })
            continue

        priority = calculate_priority(issue, waiting_hours)
        routing = route_ticket(issue, priority, now)
        ticket = _new_ticket(issue, priority, routing, now, case_id)
        CASES.setdefault(case_id, []).append(ticket["id"])

        results.append({
            "ticket": {k: v for k, v in ticket.items() if k != "created_at"} | {"created_at": ticket["created_at"].isoformat()},
            "handoff_summary": _masked_handoff(ticket),
        })

    return {"case_id": case_id, "results": results}


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return {"error": "not found"}
    refresh_sla_status(ticket)
    out = dict(ticket)
    out["created_at"] = ticket["created_at"].isoformat()
    return out


@router.get("/tickets/{ticket_id}/handoff")
async def get_handoff(ticket_id: str):
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        return {"error": "not found"}
    refresh_sla_status(ticket)
    return _masked_handoff(ticket)


@router.get("/tickets")
async def list_tickets():
    out = []
    for t in TICKETS.values():
        refresh_sla_status(t)
        out.append({
            "id": t["id"], "priority": t["priority"], "status": t["status"],
            "team": t["routing"].get("team"), "sla_pct_consumed": t["sla_pct_consumed"],
        })
    return {"count": len(out), "tickets": out}


@router.post("/tickets/sweep")
async def manual_sweep():
    now = datetime.utcnow()
    for t in TICKETS.values():
        refresh_sla_status(t, now)
    return {"swept": len(TICKETS), "at": now.isoformat()}


@router.get("/config")
async def get_config():
    return {"config": CONFIG, "teams": TEAMS}


@router.post("/config/business-rules")
async def update_business_rules(payload: dict = Body(...)):
    """Runtime update of business_days / business_start / business_end /
    holidays / sla_hours / warning_threshold — any subset. No redeploy needed."""
    for key in ["business_days", "business_start", "business_end", "holidays", "sla_hours", "warning_threshold", "duplicate_window_hours"]:
        if key in payload:
            CONFIG[key] = payload[key]
    return {"updated": True, "config": CONFIG}


@router.post("/config/teams/{team_name}/agents/{agent_id}")
async def update_agent_availability(team_name: str, agent_id: str, payload: dict = Body(...)):
    """Toggle an agent's availability/workload/capacity at runtime — used to
    simulate an unavailable team (all agents set available=false)."""
    team = TEAMS.get(team_name)
    if not team:
        return {"error": "unknown team"}
    for agent in team["agents"]:
        if agent["id"] == agent_id:
            for key in ["available", "workload", "capacity"]:
                if key in payload:
                    agent[key] = payload[key]
            return {"updated": True, "agent": agent}
    return {"error": "unknown agent"}