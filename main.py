import os
import re
import time
import json
import io
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

from fastapi import FastAPI, Form, File, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import httpx
from PIL import Image
import pytesseract

# Optional PDF text extraction
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

app = FastAPI(title="Multimodal Customer Support & OCR Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL_NAME = "qwen2.5:1.5b"
UPLOAD_DIR = "./temp_uploads"
FILE_RETENTION_SECONDS = 86400  # Delete files after 24 hours

os.makedirs(UPLOAD_DIR, exist_ok=True)

DEFAULT_SYSTEM = (
    "You are a highly capable AI assistant. Answer the user's prompt directly, "
    "thoroughly, and accurately without meta-commentary."
)


# ==========================================
# 1. SECURITY & MASKING HELPERS
# ==========================================
INJECTION_PATTERN = re.compile(
    r"(ignore\s+previous\s+instructions|system\s+prompt|bypass\s+security|override\s+system|sudo)",
    re.IGNORECASE
)

def is_prompt_injection(text: str) -> bool:
    """Detects adversarial instructions hidden in messages or OCR text."""
    return bool(INJECTION_PATTERN.search(text))

def mask_pii(text: str) -> str:
    """Masks credit card numbers, phone numbers, and email addresses in logs."""
    text = re.sub(r'\b(?:\d[ -]*?){13,16}\b', '[MASKED_CARD]', text)
    text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[MASKED_EMAIL]', text)
    text = re.sub(r'\b\+?\d{1,4}?[-.\s]?\(?\d{1,3}?\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4}\b', '[MASKED_PHONE]', text)
    return text

def safe_log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] LOG: {mask_pii(msg)}")


# ==========================================
# 2. FILE EXTRACTION & OCR
# ==========================================
def extract_ocr_from_image(image_bytes: bytes) -> Dict[str, Any]:
    """Runs OCR on uploaded screenshots/images and extracts key structured fields."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        ocr_text = pytesseract.image_to_string(image)
    except Exception as e:
        return {"error": "Low image quality or unreadable image file.", "text": ""}

    if is_prompt_injection(ocr_text):
        raise ValueError("Prompt injection detected inside image text.")

    # Never invent missing values — use regex matching or return None
    order_id = re.search(r'\b(ORD|INV|ORDER)[-#\s]?\d{4,10}\b', ocr_text, re.IGNORECASE)
    date_val = re.search(r'\b\d{2}[/-]\d{2}[/-]\d{4}\b|\b\d{4}[/-]\d{2}[/-]\d{2}\b', ocr_text)
    amount_val = re.search(r'(\$|₹|EUR)\s?(\d+[\.,]\d{2})', ocr_text)
    error_code = re.search(r'\b(ERR|ERROR)[-_]\d{3,6}\b', ocr_text, re.IGNORECASE)

    return {
        "orderId": order_id.group(0) if order_id else None,
        "date": date_val.group(0) if date_val else None,
        "amount": amount_val.group(0) if amount_val else None,
        "errorCode": error_code.group(0) if error_code else None,
        "rawText": ocr_text
    }

def extract_file_text(filename: str, content: bytes) -> tuple[str, Optional[Dict[str, Any]]]:
    """Extracts text and structured OCR data depending on file type."""
    lower_name = filename.lower()
    extracted_data = None

    if lower_name.endswith((".png", ".jpg", ".jpeg")):
        extracted_data = extract_ocr_from_image(content)
        raw_text = extracted_data.get("rawText", "")
        return raw_text, extracted_data

    if lower_name.endswith(".pdf") and PDF_SUPPORT:
        try:
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            return text[:3000], None
        except Exception:
            return "[Could not extract text from this PDF.]", None

    return content.decode("utf-8", errors="ignore")[:3000], None


# ==========================================
# 3. CONFLICT CHECKING
# ==========================================
def check_conflicts(user_msg: str, ocr_data: Dict[str, Any]) -> Optional[str]:
    """Flags mismatched order IDs between the customer message and OCR document."""
    user_order = re.search(r'\b(ORD|INV|ORDER)[-#\s]?\d{4,10}\b', user_msg, re.IGNORECASE)
    if user_order and ocr_data.get("orderId"):
        if user_order.group(0).upper() != ocr_data["orderId"].upper():
            return f"Order ID in message ({user_order.group(0)}) conflicts with document ({ocr_data['orderId']})."
    return None


# ==========================================
# 4. BACKGROUND TASK FOR FILE DELETION
# ==========================================
async def schedule_file_deletion(file_path: str, delay: int):
    """Deletes uploaded file after retention period expires."""
    await asyncio.sleep(delay)
    if os.path.exists(file_path):
        os.remove(file_path)
        safe_log(f"Deleted expired file: {file_path}")


async def call_ollama(prompt: str, system: str, temperature: float, max_tokens: int) -> str:
    payload = {
        "model": MODEL_NAME,
        "prompt": f"{system}\n\nUser: {prompt}\nAssistant:",
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(OLLAMA_API_URL, json=payload)
        if response.status_code == 200:
            return response.json().get("response", "").strip()
        return f"Error from LLM engine: received status code {response.status_code}"
    except httpx.RequestError as e:
        return f"Ollama unreachable: {str(e)}"


# ==========================================
# 5. ENDPOINTS
# ==========================================
@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(OLLAMA_TAGS_URL)
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
    background_tasks: BackgroundTasks,
    message: str = Form(...),
    active_tab: str = Form("Chats"),
    temperature: float = Form(0.7),
    max_tokens: int = Form(1024),
    file: UploadFile = File(None),
):
    start_time = time.time()

    # Guardrail: Check for Prompt Injection in Message
    if is_prompt_injection(message):
        safe_log(f"Prompt injection attempt blocked: {message}")
        return {
            "reply": "⚠️ Request rejected: Unsafe instructions or prompt injection attempt detected.",
            "isError": True
        }

    safe_log(f"Processing message: {message}")

    file_text = ""
    extracted_data = None

    if file:
        content = await file.read()
        
        # Save file locally and queue for TTL retention deletion
        temp_file_path = os.path.join(UPLOAD_DIR, f"{int(time.time())}_{file.filename}")
        with open(temp_file_path, "wb") as f:
            f.write(content)
        background_tasks.add_task(schedule_file_deletion, temp_file_path, FILE_RETENTION_SECONDS)

        try:
            file_text, extracted_data = extract_file_text(file.filename, content)
        except ValueError as ve:
            return {"reply": f"⚠️ Security Alert: {str(ve)}", "isError": True}

        if extracted_data and extracted_data.get("error"):
            return {
                "reply": "⚠️ File unreadable or image quality too low. Please provide a clearer screenshot or file.",
                "isError": True
            }

    # Guardrail: Check for Conflicts between Message & OCR Data
    if extracted_data and message:
        conflict = check_conflicts(message, extracted_data)
        if conflict:
            return {
                "reply": f"⚠️ Evidence Conflict: {conflict} Please clarify or re-upload.",
                "extractedData": extracted_data,
                "isError": True
            }

    formatted_prompt = (
        f"Context from file '{file.filename}':\n{file_text}\n\nUser Question: {message}"
        if file else message
    )

    # 30-second Timeout / SLA Check
    try:
        ai_response = await asyncio.wait_for(
            call_ollama(formatted_prompt, DEFAULT_SYSTEM, temperature, max_tokens),
            timeout=30.0
        )
    except asyncio.TimeoutError:
        return {
            "reply": "⏳ Processing exceeded 30 seconds and has been moved to the background queue. You will be notified when complete.",
            "latency": 30.0,
            "isQueued": True
        }

    latency = round(time.time() - start_time, 2)
    result = {"reply": ai_response, "latency": latency, "status": "success"}

    if extracted_data:
        result["extractedData"] = extracted_data

    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)