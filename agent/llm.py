import os
import logging
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from functools import lru_cache

# ────────────────────────────────
# 🛠️ Logging
# ────────────────────────────────
logger = logging.getLogger("agent.llm")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ────────────────────────────────
# 🔐 Environment Setup
# ────────────────────────────────
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    logger.warning("Missing GROQ_API_KEY in .env file")

# ────────────────────────────────
# 🧠 Model Profiles (UPDATED)
# ────────────────────────────────
# We are using the newest supported Groq models
EXTRACTION_MODEL = "llama-3.3-70b-versatile"  # Heavy model: High reasoning for complex JSON extraction
CLEANING_MODEL = "llama-3.1-8b-instant"       # Light model: Fast and cheap for stripping email signatures

# ────────────────────────────────
# 🤖 LLM Singleton
# ────────────────────────────────
@lru_cache(maxsize=2)
def get_llm(task_type="extraction", temperature=0.0):
    """
    Lazily load the Groq model once per task type and cache it globally.
    task_type: 'extraction' (default) or 'cleaning'
    """
    if not GROQ_API_KEY:
        raise RuntimeError("Missing GROQ_API_KEY in .env file")

    # Select the right model based on the job
    model_name = CLEANING_MODEL if task_type == "cleaning" else EXTRACTION_MODEL

    llm = ChatGroq(
        temperature=temperature,
        model_name=model_name,
        groq_api_key=GROQ_API_KEY,
        max_tokens=8192,
    )
    logger.info(f"✅ Groq model initialized for {task_type}: {model_name}")
    return llm