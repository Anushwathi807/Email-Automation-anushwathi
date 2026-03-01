from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.routes import router as email_router
from agent.inbox_watcher import watcher_loop, _poll_once, _load_results, WATCHED_EMAIL, POLL_INTERVAL_SECONDS
import asyncio
import logging

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# ── Background Watcher Task ──────────────────────────────
_watcher_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the inbox watcher when the server boots, stop on shutdown."""
    global _watcher_task
    logger.info(f"🔄 Starting continuous inbox watcher for {WATCHED_EMAIL} (every {POLL_INTERVAL_SECONDS}s)")
    _watcher_task = asyncio.create_task(watcher_loop())
    yield
    # Shutdown
    if _watcher_task:
        _watcher_task.cancel()
        logger.info("🛑 Inbox watcher stopped.")

# App Initialization
app = FastAPI(
    title="Email Extraction API",
    version="2.0",
    description="Extracts structured shift information from Gmail threads using LangChain + Groq Llama models. "
                f"Continuously monitors {WATCHED_EMAIL} for incoming job-allocation emails.",
    lifespan=lifespan,
)

# Include API routes
app.include_router(email_router)

@app.get("/")
def root():
    return {
        "message": "🚀 Email Extraction Service is running!",
        "watcher": f"Monitoring {WATCHED_EMAIL} every {POLL_INTERVAL_SECONDS}s",
    }

@app.get("/api/watcher_status")
async def watcher_status():
    """Check the current state of the inbox watcher and its extracted results."""
    results = _load_results()
    valid_count = sum(1 for r in results if r.get("valid_thread"))
    return {
        "watched_email": WATCHED_EMAIL,
        "poll_interval_seconds": POLL_INTERVAL_SECONDS,
        "total_extracted": len(results),
        "valid_staffing_threads": valid_count,
        "results": results,
    }

@app.get("/api/watcher_trigger")
async def watcher_trigger():
    """Force an immediate poll cycle (don't wait for the next interval)."""
    summary = await _poll_once()
    return {"triggered": True, **summary}