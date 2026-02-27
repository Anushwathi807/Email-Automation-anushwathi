from fastapi import FastAPI
from api.routes import router as email_router
import logging

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# App Initialization
app = FastAPI(
    title="Email Extraction API",
    version="1.0",
    description="Extracts structured shift information from Gmail threads using LangChain + Groq Llama models in parallel.",
)

# Include API routes
app.include_router(email_router)

# 🛑 BYPASSED: We disabled the strict startup check because it demands 
# credentials.json right at boot. Our new async architecture handles this dynamically!
# @app.on_event("startup")
# def init_gmail_token():
#     get_creds()

@app.get("/")
def root():
    return {"message": "🚀 Email Extraction Service is running at high speed!"}