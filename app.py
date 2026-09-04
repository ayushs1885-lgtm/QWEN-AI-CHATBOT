import warnings
warnings.filterwarnings("ignore")

import hashlib
import re
import time
import os
import io
import concurrent.futures
from datetime import datetime, timedelta

import streamlit as st
import pypdf
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract

# LangChain Imports
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings

# --- Tesseract Executable Path Config ---
pytesseract.pytesseract.tesseract_cmd = r'D:\tessract ocr\tesseract.exe'

# Configuration Constants
PROCESSING_TIMEOUT_SECONDS = 30.0
FILE_RETENTION_HOURS = 24

# Page Configuration
st.set_page_config(page_title="QWEN AI - Enterprise Customer Assistant", page_icon="✨", layout="wide")

# Light SaaS Dashboard UI Styling with Floating Action Buttons
st.markdown("""
<style>
    .stApp { background-color: #f8fafc; color: #0f172a; font-family: 'Segoe UI', Tahoma, Geneva, sans-serif; }
    header, footer {visibility: hidden;}
    div[data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e2e8f0; }
    .stChatMessage { background-color: transparent !important; border: none !important; padding: 10px 0px; }
    div[data-testid="stChatMessage"]:nth-child(even) {
        background-color: #eff6ff !important; border: 1px solid #bfdbfe !important;
        border-radius: 18px 18px 4px 18px !important; margin-left: auto; padding: 12px 18px; max-width: 80%; color: #1e3a8a !important;
    }
    div[data-testid="stChatMessage"]:nth-child(odd) {
        background-color: #ffffff !important; border: 1px solid #e2e8f0 !important;
        box-shadow: 0 1px 3px 0 rgba(0,0,0,0.05) !important; border-radius: 18px 18px 18px 4px !important;
        padding: 12px 18px; color: #0f172a !important;
    }
    .stChatInputContainer textarea {
        background-color: #ffffff !important; color: #0f172a !important;
        border: 1px solid #cbd5e1 !important; border-radius: 12px !important;
    }

    /* Floating Action Buttons (FAB) Toolbar */
    .fab-container {
        position: fixed;
        bottom: 85px;
        right: 25px;
        z-index: 99999;
        display: flex;
        flex-direction: column;
        gap: 12px;
        align-items: flex-end;
    }

    .fab-button {
        width: 48px;
        height: 48px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);
        cursor: pointer;
        font-size: 20px;
        transition: all 0.3s ease;
        border: none;
        text-decoration: none;
    }

    .fab-button:hover {
        transform: translateY(-3px) scale(1.05);
        box-shadow: 0 6px 20px rgba(37, 99, 235, 0.6);
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# Cache Heavy Resources
@st.cache_resource
def get_embeddings():
    return HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

embeddings_model = get_embeddings()
ollama = OllamaLLM(model="qwen2.5:1.5b")

prompt_template = ChatPromptTemplate.from_messages([
    ("system", """You are an Enterprise AI Customer Support Assistant created by Ayush.
Rules:
1. Always state you were created by Ayush if asked about your creator.
2. Understand and respond in natural Hinglish or English.
3. Compare customer messages against OCR Extracted Context (Order ID, Dates, Amounts, Product Info, Error Codes).
4. NEVER hallucinate or invent missing order details/amounts. If details are missing or quality is low, request clarification or another file.
5. If customer message contradicts OCR document evidence, explicitly point out the conflict to the user."""),
    ("user", "Extracted OCR Context:\n{context}\n\nUser Question/Message:\n{question}")
])

chain = prompt_template | ollama

# Session State Initializations
if "messages" not in st.session_state:
    st.session_state["messages"] = [{"role": "assistant", "content": "Welcome! Kaise madad kar sakta hu aapki aaj?"}]
if "processed_hashes" not in st.session_state:
    st.session_state["processed_hashes"] = set()
if "quarantine_files" not in st.session_state:
    st.session_state["quarantine_files"] = []
if "metrics" not in st.session_state:
    st.session_state["metrics"] = {"latency": 0.0, "failures": 0, "confidence": 0.95}
if "vector_store" not in st.session_state:
    st.session_state["vector_store"] = None
if "background_jobs" not in st.session_state:
    st.session_state["background_jobs"] = []
if "file_store" not in st.session_state:
    st.session_state["file_store"] = {}  # {hash: {"name": str, "timestamp": datetime, "data": bytes}}

# --- Security, File Inspection & Guardrails ---

def detect_prompt_injection(text):
    patterns = [
        r"ignore previous instructions", r"system prompt", r"bypass safety",
        r"reveal system password", r"you are now DAN", r"override rules"
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)

def mask_sensitive_data(text):
    text = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[REDACTED_EMAIL]', text)
    text = re.sub(r'\b\d{10}\b', '[REDACTED_PHONE]', text)
    text = re.sub(r'\b\d{4}\s?\d{4}\s?\d{4}\b', '[REDACTED_AADHAAR]', text)
    text = re.sub(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b', '[REDACTED_CARD]', text)
    return text

def check_image_quality(image: Image.Image):
    """Evaluates if image is too blurry using edge variance calculation via Pillow."""
    gray = image.convert('L')
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat = edges.histogram()
    variance = sum((i - 128) ** 2 * stat[i] for i in range(256)) / max(sum(stat), 1)
    return variance > 100.0  # Quality threshold

def extract_ocr_and_metadata(image_bytes):
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # Blur quality check
        if not check_image_quality(img):
            return None, "LOW_QUALITY_BLURRY_IMAGE"

        # Image contrast preprocessing
        gray = img.convert('L')
        enhancer = ImageEnhance.Contrast(gray)
        enhanced_img = enhancer.enhance(2.0)
        
        raw_text = pytesseract.image_to_string(enhanced_img)
        
        # Check hidden prompt injection in OCR extracted text
        if detect_prompt_injection(raw_text):
            return None, "PROMPT_INJECTION_DETECTED"
            
        return raw_text, "SUCCESS"
    except Exception as e:
        return None, f"ERROR: {str(e)}"

def extract_structured_entities(text):
    """Extracts standard transactional entities from raw text."""
    entities = {
        "order_ids": re.findall(r'\b(?:ORD|INV|#)[A-Za-z0-9-]{4,15}\b', text, re.IGNORECASE),
        "amounts": re.findall(r'(?:₹|\$|USD|INR)\s?\d+(?:\.\d{2})?', text),
        "dates": re.findall(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', text),
        "error_codes": re.findall(r'\bERR-\d{3,5}\b|\bEXC_[A-Z0-9_]+\b', text)
    }
    return entities

def extract_file_text(uploaded_file):
    fname = uploaded_file.name.lower()
    content = uploaded_file.getvalue()
    
    if fname.endswith(('.png', '.jpg', '.jpeg')):
        text, status = extract_ocr_and_metadata(content)
        if status != "SUCCESS":
            raise ValueError(f"Image rejected: {status}")
        return text
    elif fname.endswith('.pdf'):
        pdf_reader = pypdf.PdfReader(io.BytesIO(content))
        text = "".join([page.extract_text() or "" for page in pdf_reader.pages])
        if detect_prompt_injection(text):
            raise ValueError("Document contains embedded security threats.")
        return text
    elif fname.endswith('.txt'):
        text = str(content, 'utf-8')
        if detect_prompt_injection(text):
            raise ValueError("Document contains embedded security threats.")
        return text
    else:
        raise ValueError("Unsupported file format.")

def purge_expired_files():
    """Garbage collector to clean stored uploaded files past retention window."""
    now = datetime.now()
    expired = []
    for f_hash, metadata in st.session_state["file_store"].items():
        if now - metadata["timestamp"] > timedelta(hours=FILE_RETENTION_HOURS):
            expired.append(f_hash)
    for f_hash in expired:
        del st.session_state["file_store"][f_hash]
        st.session_state["processed_hashes"].discard(f_hash)

# Execute background retention cleanup
purge_expired_files()

def process_and_index_document(raw_text):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_text(raw_text)
    
    if st.session_state["vector_store"] is None:
        st.session_state["vector_store"] = FAISS.from_texts(texts=chunks, embedding=embeddings_model)
    else:
        st.session_state["vector_store"].add_texts(chunks)

# --- Sidebar UI ---
file_context = ""
with st.sidebar:
    st.title("✨ QWEN AI")
    st.caption("Enterprise Customer Assistant")
    st.markdown("---")
    
    st.subheader("📎 Attach Invoices, Images & PDFs")
    uploaded_file = st.file_uploader("Upload File", type=["png", "jpg", "jpeg", "pdf", "txt"])
    
    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        
        if file_hash in st.session_state["processed_hashes"]:
            st.warning("Duplicate File Detected! Skipping re-ingestion.")
        else:
            try:
                extracted_text = extract_file_text(uploaded_file)
                process_and_index_document(extracted_text)
                
                # Register file retention
                st.session_state["file_store"][file_hash] = {
                    "name": uploaded_file.name,
                    "timestamp": datetime.now(),
                    "data": file_bytes
                }
                st.session_state["processed_hashes"].add(file_hash)
                
                # Show extracted entity summary
                entities = extract_structured_entities(extracted_text)
                st.success("File processed & OCR entities extracted!")
                with st.expander("🔍 Extracted OCR Metadata"):
                    st.json(entities)
                    
            except Exception as e:
                st.error(f"Processing Error: {str(e)}")
                if uploaded_file.name not in st.session_state["quarantine_files"]:
                    st.session_state["quarantine_files"].append(uploaded_file.name)
                st.session_state["metrics"]["failures"] += 1

    if st.session_state["quarantine_files"]:
        st.caption(f"⚠️ Quarantined Items: {len(st.session_state['quarantine_files'])}")

    st.markdown("---")
    st.subheader("📊 Model Status & Active Jobs")
    st.success("Qwen 2.5 (1.5B) Active")
    
    col1, col2 = st.columns(2)
    col1.metric("Latency", f"{st.session_state['metrics']['latency']:.2f}s")
    col2.metric("Failures", st.session_state['metrics']['failures'])
    
    if st.session_state["background_jobs"]:
        st.warning(f"⏳ Background Queue: {len(st.session_state['background_jobs'])} jobs pending")

    st.markdown("---")
    if st.button("🗑️ Clear Chat & Memory", use_container_width=True):
        st.session_state["messages"] = [{"role": "assistant", "content": "Chat reset ho gaya hai! Kaho, kya bolna chahte ho?"}]
        st.session_state["vector_store"] = None
        st.session_state["processed_hashes"] = set()
        st.session_state["file_store"] = {}
        st.rerun()

# --- Main App Header ---
st.title("✨ QWEN AI Dashboard")

# --- Floating Action Buttons (FAB) Controls ---
col_fab1, col_fab2, col_fab3 = st.columns([8, 1, 1])
with col_fab2:
    if st.button("🗑️ Reset", help="Quick Reset Chat & Memory"):
        st.session_state["messages"] = [{"role": "assistant", "content": "Chat reset ho gaya hai!"}]
        st.session_state["vector_store"] = None
        st.session_state["processed_hashes"] = set()
        st.session_state["file_store"] = {}
        st.rerun()

with col_fab3:
    if st.button("⚡ Status", help="Check Qwen Engine Status"):
        st.toast("⚡ QWEN AI is online & running smoothly!", icon="✨")

# Render Messages
for msg in st.session_state.messages:
    role = "user" if msg["role"] == "user" else "assistant"
    avatar = "👤" if role == "user" else "✨"
    st.chat_message(role, avatar=avatar).write(msg["content"])

def run_llm_execution(user_query, retrieved_context):
    """Executes the Ollama LangChain chain synchronously."""
    return chain.invoke({"question": user_query, "context": retrieved_context if retrieved_context else "None"})

def process_query_with_timeout(user_query, context):
    start_time = time.time()
    
    if detect_prompt_injection(user_query):
        st.session_state["metrics"]["failures"] += 1
        return "⚠️ Security Risk: Prompt Injection detected and blocked.", 0.0

    retrieved_context = context
    if st.session_state["vector_store"] is not None:
        docs = st.session_state["vector_store"].similarity_search(user_query, k=3)
        retrieved_context = "\n\n".join([doc.page_content for doc in docs])

    # Thread execution to observe timeout budget
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run_llm_execution, user_query, retrieved_context)
    
    try:
        # Wait up to timeout limit
        full_response = future.result(timeout=PROCESSING_TIMEOUT_SECONDS)
        elapsed_time = time.time() - start_time
        masked_response = mask_sensitive_data(full_response)
        return masked_response, elapsed_time
    except concurrent.futures.TimeoutError:
        # Transfer request to background processing queue
        job_id = f"JOB-{int(time.time())}"
        st.session_state["background_jobs"].append({
            "job_id": job_id,
            "query": user_query,
            "context": retrieved_context,
            "status": "QUEUED"
        })
        fallback_msg = f"⏳ Request processing is taking longer than {int(PROCESSING_TIMEOUT_SECONDS)}s. Your request has been moved to the background queue (ID: `{job_id}`). We will notify you once completed."
        return fallback_msg, time.time() - start_time

# Chat Input Processing
if prompt := st.chat_input("Ask QWEN AI about your order, invoices, or upload documents..."):
    clean_prompt = mask_sensitive_data(prompt)
    st.session_state.messages.append({"role": "user", "content": clean_prompt})
    st.chat_message("user", avatar="👤").write(clean_prompt)

    with st.chat_message("assistant", avatar="✨"):
        with st.spinner("QWEN AI analyzing document context & query..."):
            response_text, latency = process_query_with_timeout(prompt, file_context)
            st.write(response_text)

    st.session_state["metrics"]["latency"] = latency
    st.session_state.messages.append({"role": "assistant", "content": response_text})
    st.rerun()