import hashlib
import re
import time
import streamlit as st
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
import pypdf

# Page Configuration
st.set_page_config(page_title="QWEN AI CHATBOT", page_icon="✨", layout="wide")

# Light SaaS Dashboard UI Styling
st.markdown("""
<style>
    .stApp {
        background-color: #f8fafc;
        color: #0f172a;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }
    header, footer {visibility: hidden;}

    div[data-testid="stSidebar"] {
        background-color: #ffffff !important;
        border-right: 1px solid #e2e8f0;
    }

    .stChatMessage {
        background-color: transparent !important;
        border: none !important;
        padding: 10px 0px;
    }
    
    div[data-testid="stChatMessage"]:nth-child(even) {
        background-color: #eff6ff !important;
        border: 1px solid #bfdbfe !important;
        border-radius: 18px 18px 4px 18px !important;
        margin-left: auto;
        padding: 12px 18px;
        max-width: 80%;
        color: #1e3a8a !important;
    }
    
    div[data-testid="stChatMessage"]:nth-child(odd) {
        background-color: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05) !important;
        border-radius: 18px 18px 18px 4px !important;
        padding: 12px 18px;
        color: #0f172a !important;
    }

    .stChatInputContainer textarea {
        background-color: #ffffff !important;
        color: #0f172a !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 12px !important;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05) !important;
    }

    .stChatInputContainer textarea:focus {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.2) !important;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Ollama Model
ollama = OllamaLLM(model="qwen2.5:1.5b")

prompt_template = ChatPromptTemplate.from_messages([
    ("system", """You are an AI assistant created by Ayush.
    Rules:
    1. Always state you were created by Ayush if asked about your developer/creator.
    2. Understand and respond in fluent, natural Hinglish (Hindi written in Roman script) or English.
    3. Keep your tone friendly, helpful, and natural.
    4. Use the provided context accurately to answer the user question."""),
    ("user", "Context: {context}\n\nQuestion: {question}")
])

chain = prompt_template | ollama

# Initialize Session States
if "messages" not in st.session_state:
    st.session_state["messages"] = [
        {"role": "assistant", "content": "Welcome! Kaise madad kar sakta hu aapki aaj?"}
    ]
if "processed_hashes" not in st.session_state:
    st.session_state["processed_hashes"] = set()
if "quarantine_files" not in st.session_state:
    st.session_state["quarantine_files"] = []
if "metrics" not in st.session_state:
    st.session_state["metrics"] = {"latency": 0.0, "failures": 0, "confidence": 0.95}

# Security & Guardrail Functions
def detect_prompt_injection(user_input):
    injection_patterns = [
        r"ignore previous instructions",
        r"system prompt",
        r"bypass safety",
        r"reveal system password"
    ]
    for pattern in injection_patterns:
        if re.search(pattern, user_input, re.IGNORECASE):
            return True
    return False

def mask_sensitive_data(text):
    text = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[REDACTED_EMAIL]', text)
    text = re.sub(r'\b\d{10}\b', '[REDACTED_PHONE]', text)
    text = re.sub(r'\b\d{4}\s?\d{4}\s?\d{4}\b', '[REDACTED_AADHAAR]', text)
    return text

def extract_file_text(uploaded_file):
    if uploaded_file.name.endswith('.txt'):
        return str(uploaded_file.read(), 'utf-8')
    elif uploaded_file.name.endswith('.pdf'):
        pdf_reader = pypdf.PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() or ""
        return text
    return ""

# --- Sidebar Component ---
file_context = ""
with st.sidebar:
    st.title("⚡ Dashboard")
    st.markdown("---")
    
    st.subheader("📎 Attach Files & Images")
    uploaded_file = st.file_uploader(
        "Upload Document (PDF/TXT/Image)", 
        type=["png", "jpg", "jpeg", "pdf", "txt"]
    )
    
    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        
        if uploaded_file.name.split('.')[-1].lower() not in ["png", "jpg", "jpeg", "pdf", "txt"]:
            st.error("File Quarantined: Unsupported format!")
            if uploaded_file.name not in st.session_state["quarantine_files"]:
                st.session_state["quarantine_files"].append(uploaded_file.name)
        elif file_hash in st.session_state["processed_hashes"]:
            st.warning("Duplicate File Detected! Skipping re-ingestion.")
        else:
            try:
                if uploaded_file.type.startswith("image"):
                    st.image(uploaded_file, caption="Uploaded Image Preview", use_container_width=True)
                    file_context = f"[Attached image: {uploaded_file.name}]"
                else:
                    file_context = extract_file_text(uploaded_file)
                    st.success("File processed & active in knowledge base.")
                st.session_state["processed_hashes"].add(file_hash)
            except Exception as e:
                st.error("File processing failed! Quarantined.")
                st.session_state["quarantine_files"].append(uploaded_file.name)
                st.session_state["metrics"]["failures"] += 1

    if st.session_state["quarantine_files"]:
        st.caption(f"⚠️ Quarantined Items: {len(st.session_state['quarantine_files'])}")

    st.markdown("---")
    st.subheader("📊 Model Status & Health")
    st.success("Qwen 2.5 (1.5B) Active")
    
    # Live Latency and Failure Display
    col1, col2 = st.columns(2)
    col1.metric("Latency", f"{st.session_state['metrics']['latency']:.2f}s")
    col2.metric("Failures", st.session_state['metrics']['failures'])

    st.markdown("---")
    st.subheader("Controls")
    if st.button("🗑️ Clear Chat History", use_container_width=True):
        st.session_state["messages"] = [
            {"role": "assistant", "content": "Chat reset ho gaya hai! Kaho, kya bolna chahte ho?"}
        ]
        st.rerun()

# --- Main Area ---
st.title("✨ QWEN AI CHATBOT")

# Render Past Chat Messages
for msg in st.session_state.messages:
    role = "user" if msg["role"] == "user" else "assistant"
    avatar = "👤" if role == "user" else "✨"
    st.chat_message(role, avatar=avatar).write(msg["content"])

# Response Generator Function
def generate_response(user_query, context):
    start_time = time.time()
    
    # Prompt Injection Security Check
    if detect_prompt_injection(user_query):
        st.session_state["metrics"]["failures"] += 1
        blocked_msg = "⚠️ Security Risk: Prompt Injection detected and blocked."
        st.session_state["full_message"] = blocked_msg
        yield blocked_msg
        return

    # LLM Stream Processing
    response = chain.stream({"question": user_query, "context": context if context else "None"})
    full_response = ""
    for token in response:
        full_response += token
        yield token
    
    # Apply Sensitive Data Masking Guardrail
    masked_response = mask_sensitive_data(full_response)
    st.session_state["full_message"] = masked_response
    
    # Measure Latency and Update Session State
    st.session_state["metrics"]["latency"] = time.time() - start_time

# Chat Input Handler
if prompt := st.chat_input("Ask Qwen AI..."):
    clean_prompt = mask_sensitive_data(prompt)
    st.session_state.messages.append({"role": "user", "content": clean_prompt})
    st.chat_message("user", avatar="👤").write(clean_prompt)

    with st.chat_message("assistant", avatar="✨"):
        st.write_stream(generate_response(prompt, file_context))

    st.session_state.messages.append(
        {"role": "assistant", "content": st.session_state["full_message"]}
    )
    st.rerun()