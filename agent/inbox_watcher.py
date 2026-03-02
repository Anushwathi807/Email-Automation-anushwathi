"""
Continuous Inbox Watcher for Specific Email Conversations (Time Machine Edition)

Polls the inbox on a fixed interval, extracts ALL historical and new job-allocation 
threads strictly between two target accounts, and persists results to output.json.
Now includes raw chat history!
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Set

from agent.token_store import read_token_json_by_email
# We import fetch_thread directly to bypass the "today only" date limits
from agent.tools import build_gmail_service_from_refresh_token, fetch_thread
from agent.agent_runner import run_agent_step_async

logger = logging.getLogger("agent.inbox_watcher")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ── Phase 1.1: Strict Target Configuration ────────────────
ACCOUNT_1 = "elsysayla@gmail.com"
ACCOUNT_2 = "anushwathiranganathan@gmail.com"
WATCHER_AUTH_EMAIL = ACCOUNT_1
CONVERSATION_QUERY = f"(from:{ACCOUNT_1} to:{ACCOUNT_2}) OR (from:{ACCOUNT_2} to:{ACCOUNT_1})"
# ──────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
RESULTS_DIR = os.getenv("WATCHER_RESULTS_DIR", "watcher_results")
PROCESSED_IDS_FILE = os.path.join(RESULTS_DIR, "processed_thread_ids.json")

# ── UPGRADE: The Main Output File ─────────────────────────
OUTPUT_JSON_FILE = "output.json" # This will save right in your main folder!
# ──────────────────────────────────────────────────────────

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
    # Save directly to the main output.json file!
    with open(OUTPUT_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

async def _poll_once() -> Dict[str, Any]:
    logger.info(f"📬 [Watcher] Scanning ALL historical & new chats between {ACCOUNT_1} & {ACCOUNT_2}...")

    token_data = read_token_json_by_email(WATCHER_AUTH_EMAIL) or {}
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        logger.error(f"[Watcher] No valid refresh_token for {WATCHER_AUTH_EMAIL}. Skipping cycle.")
        return {"status": "error", "detail": "Missing refresh_token"}

    try:
        gmail = build_gmail_service_from_refresh_token(refresh_token.strip(), token_json=token_data)
        
        # ── UPGRADE: The Time Machine ──────────────────────────────
        # We ask Gmail for ALL threads matching the query, completely ignoring the date!
        # maxResults=50 protects your API limits while grabbing all recent history.
        res = gmail.users().threads().list(userId='me', q=CONVERSATION_QUERY, maxResults=50).execute()
        thread_stubs = res.get('threads', [])
        
        processed_states = _load_processed_ids()
        new_threads = []
        
        for stub in thread_stubs:
            tid = stub['id']
            # Fetch the actual thread data to count the messages
            thread_data = fetch_thread(gmail, tid)
            
            # Skip if it's empty
            if not thread_data.get('messages'):
                continue
                
            msg_count = len(thread_data.get('messages', []))
            state_sig = f"{tid}_{msg_count}"

            if state_sig not in processed_states:
                new_threads.append(thread_data)
        # ───────────────────────────────────────────────────────────

    except Exception as e:
        logger.error(f"[Watcher] Failed to fetch threads: {e}")
        return {"status": "error", "detail": str(e)}

    if not new_threads:
        logger.info(f"[Watcher] No new replies detected.")
        return {"status": "ok", "new_threads": 0}

    logger.info(f"[Watcher] 🚨 HISTORICAL/NEW DATA DETECTED! Found {len(new_threads)} updates to process.")

    extractions = []
    for i in range(0, len(new_threads), BATCH_SIZE):
        batch = new_threads[i:i + BATCH_SIZE]
        tasks = [run_agent_step_async(thread) for thread in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        extractions.extend(batch_results)

        if i + BATCH_SIZE < len(new_threads):
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    existing_results = _load_results()
    new_result_count = 0

    for thread, extraction in zip(new_threads, extractions):
        thread_id = thread.get("threadId", "")
        state_sig = f"{thread_id}_{len(thread.get('messages', []))}"

        if isinstance(extraction, Exception):
            logger.error(f"[Watcher] Failed thread {thread_id}: {extraction}")
            continue

        extracted = extraction if isinstance(extraction, dict) else {"raw_output": str(extraction), "parsed_output": {}}
        valid_thread = bool(extracted.pop("valid_thread", False))

        entry = {
            "conversation": f"{ACCOUNT_1} <-> {ACCOUNT_2}",
            "thread_id": thread_id,
            "message_count": len(thread.get('messages', [])),
            "valid_thread": valid_thread,
            
            # ── NEW: Save the actual scraped email bodies! ──
            "scraped_chat_history": [
                {
                    "from": m.get("from"),
                    "date": m.get("date"),
                    "message_body": m.get("body")
                } 
                for m in thread.get('messages', [])
            ],
            # ────────────────────────────────────────────────
            
            "extracted": extracted,
            "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        existing_results.append(entry)
        processed_states.add(state_sig)
        new_result_count += 1
        
        logger.info(f"✨ [Watcher] EXTRACTION COMPLETE for {thread_id}:")
        print(json.dumps(extracted, indent=2))

    _save_results(existing_results)
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