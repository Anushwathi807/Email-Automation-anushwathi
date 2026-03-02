# agent/llm.py
import os
import logging
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    logger.warning("Missing GEMINI_API_KEY in .env file")

# ────────────────────────────────
# 🤖 LLM Singleton
# ────────────────────────────────
@lru_cache(maxsize=1)
def get_llm():
    """
    Lazily load the Native Google Gemini model once and cache it globally.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY in .env file")

    # Initialize the official Google Generative AI integration
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0.3,
        max_tokens=8192,
        google_api_key=GEMINI_API_KEY
    )
    logger.info("✅ Native Google Gemini model initialized (gemini-2.0-flash)")
    return llm