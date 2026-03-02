"""
Continuous Inbox Watcher for Specific Email Conversations
Now includes the Time Machine & Performance Metrics Tracker!
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import time  # ── NEW: Imported time for the stopwatch ──
from typing import Any, Dict, List, Set

from agent.token_store import read_token_json_by_email
from agent.tools import build_gmail_service_from_refresh_token, fetch_thread
from agent.agent_runner import run_agent_step_async

logger = logging.getLogger("agent.inbox_watcher")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

ACCOUNT_1 = "elsysayla@gmail.com"
ACCOUNT_2 = "anushwathiranganathan@gmail.com"
WATCHER_AUTH_EMAIL = ACCOUNT_1
CONVERSATION_QUERY = f"(from:{ACCOUNT_1} to:{ACCOUNT_2}) OR (from:{ACCOUNT_2} to:{ACCOUNT_1})"

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
RESULTS_DIR = os.getenv("WATCHER_RESULTS_DIR", "watcher_results")
PROCESSED_IDS_FILE = os.path.join(RESULTS_DIR, "processed_thread_ids.json")
OUTPUT_JSON_FILE = "output.json" 

# ── NEW: Performance Metrics File Configuration ──────────
METRICS_JSON_FILE = "performance_metrics.json"
# ─────────────────────────────────────────────────────────

BATCH_SIZE = 3
DELAY_BETWEEN_BATCHES = 5.0

def _ensure_results_dir() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)

def _load_processed_ids() -> Set[str]:
    _ensure_results_dir()
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    try:
        with open(PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()

def _save_processed_ids(ids: Set[str]) -> None:
    _ensure_results_dir()
    with open(PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False, indent=2)

def _load_results() -> List[Dict[str, Any]]:
    if not os.path.exists(OUTPUT_JSON_FILE):
        return []
    try:
        with open(OUTPUT_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save_results(results: List[Dict[str, Any]]) -> None:
    with open(OUTPUT_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

# ── NEW: Metrics Load/Save Functions ─────────────────────
def _load_metrics() -> List[Dict[str, Any]]:
    if not os.path.exists(METRICS_JSON_FILE):
        return []
    try:
        with open(METRICS_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _save_metrics(metrics: List[Dict[str, Any]]) -> None:
    with open(METRICS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
# ─────────────────────────────────────────────────────────

# ── NEW: The Stopwatch Wrapper ───────────────────────────
async def _timed_run(thread: Dict[str, Any]) -> tuple:
    """Wraps the agent runner to calculate execution time."""
    start_time = time.time()
    try:
        result = await run_agent_step_async(thread)
    except Exception as e:
        result = e
    end_time = time.time()
    duration = end_time - start_time
    return thread, result, duration
# ─────────────────────────────────────────────────────────

async def _poll_once() -> Dict[str, Any]:
    logger.info(f"📬 [Watcher] Scanning ALL historical & new chats between {ACCOUNT_1} & {ACCOUNT_2}...")

    token_data = read_token_json_by_email(WATCHER_AUTH_EMAIL) or {}
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        logger.error(f"[Watcher] No valid refresh_token for {WATCHER_AUTH_EMAIL}. Skipping cycle.")
        return {"status": "error", "detail": "Missing refresh_token"}

    try:
        gmail = build_gmail_service_from_refresh_token(refresh_token.strip(), token_json=token_data)
        res = gmail.users().threads().list(userId='me', q=CONVERSATION_QUERY, maxResults=50).execute()
        thread_stubs = res.get('threads', [])
        
        processed_states = _load_processed_ids()
        new_threads = []
        
        for stub in thread_stubs:
            tid = stub['id']
            thread_data = fetch_thread(gmail, tid)
            if not thread_data.get('messages'):
                continue
            msg_count = len(thread_data.get('messages', []))
            state_sig = f"{tid}_{msg_count}"
            if state_sig not in processed_states:
                new_threads.append(thread_data)

    except Exception as e:
        logger.error(f"[Watcher] Failed to fetch threads: {e}")
        return {"status": "error", "detail": str(e)}

    if not new_threads:
        logger.info(f"[Watcher] No new replies detected.")
        return {"status": "ok", "new_threads": 0}

    logger.info(f"[Watcher] 🚨 DATA DETECTED! Found {len(new_threads)} updates to process.")

    # Process in batches using the new Stopwatch function
    extractions_with_time = []
    for i in range(0, len(new_threads), BATCH_SIZE):
        batch = new_threads[i:i + BATCH_SIZE]
        tasks = [_timed_run(thread) for thread in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        extractions_with_time.extend(batch_results)

        if i + BATCH_SIZE < len(new_threads):
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    existing_results = _load_results()
    existing_metrics = _load_metrics() # ── NEW
    new_result_count = 0

    # Unpack the results from the timed wrapper
    for item in extractions_with_time:
        if isinstance(item, Exception):
            logger.error(f"[Watcher] Fatal batch error: {item}")
            continue
            
        thread, extraction, duration = item
        thread_id = thread.get("threadId", "")
        msg_count = len(thread.get('messages', []))
        state_sig = f"{thread_id}_{msg_count}"

        if isinstance(extraction, Exception):
            logger.error(f"[Watcher] Failed thread {thread_id}: {extraction}")
            status = "failed"
            extracted = {"raw_output": str(extraction), "parsed_output": {}}
            valid_thread = False
        else:
            extracted = extraction if isinstance(extraction, dict) else {"raw_output": str(extraction), "parsed_output": {}}
            valid_thread = bool(extracted.pop("valid_thread", False))
            status = "success" if valid_thread else "ignored (not staffing)"

        # 1. Build Output Entry
        entry = {
            "conversation": f"{ACCOUNT_1} <-> {ACCOUNT_2}",
            "thread_id": thread_id,
            "message_count": msg_count,
            "valid_thread": valid_thread,
            "scraped_chat_history": [
                {
                    "from": m.get("from"),
                    "date": m.get("date"),
                    "message_body": m.get("body")
                } 
                for m in thread.get('messages', [])
            ],
            "extracted": extracted,
            "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        # 2. Build Metrics Entry ── NEW
        metric_entry = {
            "thread_id": thread_id,
            "processing_time_seconds": round(duration, 3), # Rounds to 3 decimal places
            "messages_in_thread": msg_count,
            "llm_status": status,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        existing_results.append(entry)
        existing_metrics.append(metric_entry) # Add to metrics array
        processed_states.add(state_sig)
        new_result_count += 1
        
        logger.info(f"✨ [Watcher] EXTRACTION COMPLETE ({round(duration, 2)}s) for {thread_id}")

    # Persist both files
    _save_results(existing_results)
    _save_metrics(existing_metrics) # ── NEW
    _save_processed_ids(processed_states)

    return {"status": "ok", "new_threads": new_result_count}

async def watcher_loop() -> None:
    logger.info(f"🚀 [Watcher] Starting TIME MACHINE SCRAPER for {ACCOUNT_1} and {ACCOUNT_2}")
    await asyncio.sleep(5)

    while True:
        try:
            await _poll_once()
        except Exception as e:
            logger.error(f"[Watcher] Unexpected error: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(watcher_loop())
    except KeyboardInterrupt:
        logger.info("🛑 [Watcher] Live Scraper stopped by user.")