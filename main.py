import os
import time
import json
import base64
import httpx
from fastapi import FastAPI, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

# Optional PDF text extraction — falls back gracefully if pypdf isn't installed.
try:
    from pypdf import PdfReader
    import io
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

app = FastAPI()

from ticketing import router as ticketing_router, start_background_sweep
from rag import router as rag_router
from sentiment import router as sentiment_router, start_sentiment_sweep
app.include_router(ticketing_router)
app.include_router(rag_router)
app.include_router(sentiment_router)


@app.on_event("startup")
async def _launch_background_tasks():
    start_background_sweep()
    start_sentiment_sweep()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://qwen-ai-chatbot.vercel.app",
        "https://qwen-ai-chatbot-git-main-ayushs1885-1915s-projects.vercel.app",
        "https://myqwenbot.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Groq (cloud-hosted LLM) config ---
# GROQ_API_KEY must be set as an environment variable on Render
# (Render dashboard -> your service -> Environment -> Add Environment Variable).
# Never hardcode the key directly in this file.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
# openai/gpt-oss-20b is fast and available on Groq's free tier.
# openai/gpt-oss-120b is a larger/higher-quality alternative, still free-tier accessible.
# (llama-3.3-70b-versatile and llama-3.1-8b-instant moved to Enterprise-only access.)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
# Vision-capable model — used only when the uploaded file is an image.
# gpt-oss models are text-only, so images are routed to a multimodal model instead.
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB safety cap (Groq's own limit is 20MB per request)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}

DEFAULT_SYSTEM = (
    "You are a highly capable AI assistant. Answer the user's prompt directly, "
    "thoroughly, and accurately. Do NOT output generic template headings like "
    "'Qwen AI Analysis' or filler bullet points like 'Key focus identified'. "
    "Directly provide the complete answer."
)

OCR_SYSTEM = (
    "You extract structured data from documents. Read the provided context and "
    "the user's question, then respond with ONLY a single JSON object — no prose, "
    "no markdown fences, no commentary before or after it. Use this shape, "
    "omitting any field you cannot find:\n"
    '{"orderId": "...", "date": "...", "amount": "...", "productInfo": "...", '
    '"errorCode": "...", "confidenceScore": 0.0}\n'
    "confidenceScore is your own estimate (0 to 1) of how confident you are in "
    "the extraction."
)

TAB_SYSTEM_PROMPTS = {
    "AI Tools": OCR_SYSTEM,
}


async def call_groq(prompt: str, system: str, temperature: float, max_tokens: int) -> str:
    """
    Sends the prompt to Groq's OpenAI-compatible chat completions endpoint.
    This is a cloud-hosted API — no local server, no tunnel, no laptop uptime
    dependency. Just needs GROQ_API_KEY set as an env var on the host.
    """
    if not GROQ_API_KEY:
        return (
            "GROQ_API_KEY is not set. Add it as an environment variable in your "
            "Render service settings (Environment tab)."
        )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(GROQ_API_URL, json=payload, headers=headers)
    except httpx.RequestError as e:
        return f"Groq API is unreachable.\nDetails: {str(e)}"

    if response.status_code != 200:
        return f"Error from Groq API: received status code {response.status_code} — {response.text[:200]}"

    try:
        result = response.json()
    except json.JSONDecodeError:
        return "Error from Groq API: response was not valid JSON."

    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return "Error from Groq API: unexpected response shape."


def extract_file_text(filename: str, content: bytes, max_chars: int = 2000) -> str:
    """
    Best-effort text extraction. PDFs get real text extraction if pypdf is
    installed; everything else is treated as UTF-8 text. Truncates to max_chars.
    Images are handled separately in analyze() via the vision model — this
    function is only used for non-image attachments.
    """
    lower_name = filename.lower()

    if lower_name.endswith(".pdf") and PDF_SUPPORT:
        try:
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text[:max_chars]
        except Exception:
            return "[Could not extract text from this PDF.]"

    if lower_name.endswith(".pdf") and not PDF_SUPPORT:
        return "[PDF uploaded, but pypdf is not installed — run `pip install pypdf` for PDF text extraction.]"

    return content.decode("utf-8", errors="ignore")[:max_chars]


async def call_groq_vision(image_bytes: bytes, filename: str, question: str, system: str,
                            temperature: float, max_tokens: int) -> str:
    """
    Sends the image directly to a Groq multimodal model (no local OCR engine
    needed — Tesseract/OpenCV aren't installable as system binaries on
    Render's native Python runtime, so this avoids that dependency entirely).
    """
    if not GROQ_API_KEY:
        return (
            "GROQ_API_KEY is not set. Add it as an environment variable in your "
            "Render service settings (Environment tab)."
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:
        return f"This image is too large ({len(image_bytes)/1_048_576:.1f} MB). Please upload one under {MAX_IMAGE_BYTES // 1_048_576} MB."

    ext = "." + filename.lower().split(".")[-1] if "." in filename else ".png"
    mime = MIME_BY_EXT.get(ext, "image/png")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": GROQ_VISION_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question or "Describe what is in this image and extract any visible text."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(GROQ_API_URL, json=payload, headers=headers)
    except httpx.RequestError as e:
        return f"Groq API is unreachable.\nDetails: {str(e)}"

    if response.status_code != 200:
        return f"Error from Groq vision model: received status code {response.status_code} — {response.text[:200]}"

    try:
        result = response.json()
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, json.JSONDecodeError):
        return "Error from Groq vision model: unexpected response shape."


def try_parse_json(text: str):
    """Attempts to parse a model response as JSON (for OCR/tool responses)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


@app.get("/health")
async def health():
    """Reports whether the Groq API key is set and reachable."""
    if not GROQ_API_KEY:
        return {"status": "down", "groq_reachable": False, "reason": "GROQ_API_KEY not set"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                GROQ_MODELS_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}
            )
        if resp.status_code != 200:
            return {"status": "degraded", "groq_reachable": False, "status_code": resp.status_code}
        return {"status": "ok", "groq_reachable": True, "model": GROQ_MODEL}
    except httpx.RequestError:
        return {"status": "down", "groq_reachable": False}


@app.post("/api/analyze")
async def analyze(
    message: str = Form(...),
    active_tab: str = Form("Chats"),
    temperature: float = Form(0.7),
    max_tokens: int = Form(1024),
    file: UploadFile = File(None),
):
    start_time = time.time()
    system_prompt = TAB_SYSTEM_PROMPTS.get(active_tab, DEFAULT_SYSTEM)

    if file:
        content = await file.read()
        lower_name = file.filename.lower()

        if lower_name.endswith(IMAGE_EXTENSIONS):
            # Images go straight to the vision model — no text-extraction
            # step, since the model reads the image directly.
            ai_response = await call_groq_vision(
                content, file.filename, message, system_prompt, temperature, max_tokens
            )
        else:
            file_text = extract_file_text(file.filename, content)
            formatted_prompt = (
                f"Context from attached file '{file.filename}':\n{file_text}\n\n"
                f"User Question: {message}"
            )
            ai_response = await call_groq(formatted_prompt, system_prompt, temperature, max_tokens)
    else:
        ai_response = await call_groq(message, system_prompt, temperature, max_tokens)

    latency = round(time.time() - start_time, 2)

    result = {
        "reply": ai_response,
        "latency": latency,
        "status": "success",
    }

    if active_tab == "AI Tools":
        parsed = try_parse_json(ai_response)
        if parsed:
            result["extractedData"] = parsed
            result["reply"] = "Extraction complete — see structured data below."

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)