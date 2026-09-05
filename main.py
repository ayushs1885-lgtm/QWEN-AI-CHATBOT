from fastapi import FastAPI, Form, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
import time
import json
import httpx

# Optional PDF text extraction — falls back gracefully if pypdf isn't installed.
try:
    from pypdf import PdfReader
    import io
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

app = FastAPI()

# Scope CORS to the actual frontend origin rather than a wildcard + credentials
# (that combination is invalid per spec and browsers may reject it anyway).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://qwen-ai-chatbot.vercel.app",
        "https://qwen-ai-chatbot-git-main-ayushs1885-1915s-projects.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# NOTE: this points at a localtunnel URL exposing your own machine's Ollama
# instance (localhost:11434). It only works while that tunnel is running AND
# your laptop + Ollama are on. Update this line each time you restart the
# tunnel, since localtunnel gives you a new URL every time unless you use a
# fixed subdomain (see note at the bottom of this file).
OLLAMA_API_URL = "https://beige-ways-appear.loca.lt/api/generate"
OLLAMA_TAGS_URL = "https://beige-ways-appear.loca.lt/api/tags"
MODEL_NAME = "qwen2.5:1.5b"

# System instructions per "AI Tools" tab. Anything not listed uses DEFAULT_SYSTEM.
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
    "AI Tools": OCR_SYSTEM,  # only meaningfully differs for the OCR Extractor tool;
                              # other AI Tools reuse the default assistant behavior.
}


async def call_ollama(prompt: str, system: str, temperature: float, max_tokens: int) -> str:
    """
    Sends prompt to the local Ollama instance asynchronously so the FastAPI
    event loop isn't blocked while waiting on generation.
    """
    full_prompt = f"{system}\n\nUser: {prompt}\nAssistant:"

    payload = {
        "model": MODEL_NAME,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # localtunnel shows an HTML warning page to browsers unless this
            # header is sent — without it, every request here would get back
            # an HTML page instead of Ollama's JSON response.
            response = await client.post(
                OLLAMA_API_URL,
                json=payload,
                headers={"bypass-tunnel-reminder": "true"},
            )
    except httpx.RequestError as e:
        return (
            "Ollama/LLM Server is unreachable. Please ensure Ollama is running.\n"
            f"Details: {str(e)}"
        )

    if response.status_code != 200:
        return f"Error from LLM engine: received status code {response.status_code}"

    try:
        result = response.json()
    except json.JSONDecodeError:
        return "Error from LLM engine: response was not valid JSON."

    return result.get("response", "").strip()


def extract_file_text(filename: str, content: bytes, max_chars: int = 2000) -> str:
    """
    Best-effort text extraction. PDFs get real text extraction if pypdf is
    installed; everything else is treated as UTF-8 text (fine for .txt, best
    effort for anything else). Truncates to max_chars either way.
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

    if lower_name.endswith((".png", ".jpg", ".jpeg")):
        return "[Image uploaded — OCR is not enabled on this backend yet.]"

    return content.decode("utf-8", errors="ignore")[:max_chars]


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
    """Lets the frontend show real backend/model status instead of relying
    only on /api/analyze failures after the fact."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                OLLAMA_TAGS_URL, headers={"bypass-tunnel-reminder": "true"}
            )
        if resp.status_code != 200:
            return {"status": "degraded", "ollama_reachable": False}
        models = [m.get("name") for m in resp.json().get("models", [])]
        return {
            "status": "ok",
            "ollama_reachable": True,
            "model_loaded": MODEL_NAME in models,
            "available_models": models,
        }
    except httpx.RequestError:
        return {"status": "down", "ollama_reachable": False}


@app.post("/api/analyze")
async def analyze(
    message: str = Form(...),
    active_tab: str = Form("Chats"),
    temperature: float = Form(0.7),
    max_tokens: int = Form(1024),
    file: UploadFile = File(None),
):
    start_time = time.time()

    if file:
        content = await file.read()
        file_text = extract_file_text(file.filename, content)
        formatted_prompt = (
            f"Context from attached file '{file.filename}':\n{file_text}\n\n"
            f"User Question: {message}"
        )
    else:
        formatted_prompt = message

    system_prompt = TAB_SYSTEM_PROMPTS.get(active_tab, DEFAULT_SYSTEM)

    ai_response = await call_ollama(formatted_prompt, system_prompt, temperature, max_tokens)

    latency = round(time.time() - start_time, 2)

    result = {
        "reply": ai_response,
        "latency": latency,
        "status": "success",
    }

    # If this was an OCR/extraction-style call, try to surface structured data
    # for the frontend's `extractedData` rendering.
    if active_tab == "AI Tools":
        parsed = try_parse_json(ai_response)
        if parsed:
            result["extractedData"] = parsed
            result["reply"] = "Extraction complete — see structured data below."

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)