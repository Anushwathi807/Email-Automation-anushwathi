# agent/inbox_watcher.py
"""
Continuous Inbox Watcher for elsysayla@gmail.com

Polls the inbox on a fixed interval, extracts new job-allocation threads,
and persists results to a JSON file. Skips threads it has already processed.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

from agent.token_store import read_token_json_by_email
from agent.tools import build_gmail_service_from_refresh_token, get_threads_for_date_with_service
from agent.agent_runner import run_agent_step_async

logger = logging.getLogger("agent.inbox_watcher")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ── Configuration ────────────────────────────────────────
WATCHED_EMAIL = os.getenv("WATCHED_EMAIL", "elsysayla@gmail.com")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))  # Default: 5 min
RESULTS_DIR = os.getenv("WATCHER_RESULTS_DIR", "watcher_results")
PROCESSED_IDS_FILE = os.path.join(RESULTS_DIR, "processed_thread_ids.json")
RESULTS_FILE = os.path.join(RESULTS_DIR, "extracted_results.json")

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
    _ensure_results_dir()
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_results(results: List[Dict[str, Any]]) -> None:
    _ensure_results_dir()
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


async def _poll_once() -> Dict[str, Any]:
    """
    Single poll cycle:
    1. Fetch today's threads for WATCHED_EMAIL
    2. Filter out already-processed thread IDs
    3. Run LLM extraction on new threads
    4. Persist results and update processed IDs
    Returns a summary dict.
    """
    today = datetime.date.today().isoformat()
    logger.info(f"📬 [Watcher] Polling {WATCHED_EMAIL} for date={today}...")

    # Load token
    token_data = read_token_json_by_email(WATCHED_EMAIL) or {}
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        logger.error(f"[Watcher] No valid refresh_token for {WATCHED_EMAIL}. Skipping cycle.")
        return {"status": "error", "detail": "Missing refresh_token"}

    # Build Gmail service and fetch threads
    try:
        gmail = build_gmail_service_from_refresh_token(refresh_token.strip(), token_json=token_data)
        threads: List[Dict[str, Any]] = get_threads_for_date_with_service(gmail, today)
    except Exception as e:
        logger.error(f"[Watcher] Failed to fetch threads: {e}")
        return {"status": "error", "detail": str(e)}

    # Filter out already-processed threads
    processed_ids = _load_processed_ids()
    new_threads = [t for t in threads if t.get("threadId") not in processed_ids]

    if not new_threads:
        logger.info(f"[Watcher] No new threads found (total={len(threads)}, already_processed={len(processed_ids)})")
        return {"status": "ok", "new_threads": 0, "total_threads": len(threads)}

    logger.info(f"[Watcher] Found {len(new_threads)} NEW threads to process (out of {len(threads)} total)")

    # Process in batches (same rate-limiting as routes.py)
    extractions = []
    for i in range(0, len(new_threads), BATCH_SIZE):
        batch = new_threads[i:i + BATCH_SIZE]
        logger.info(f"[Watcher] Firing batch {i // BATCH_SIZE + 1} ({len(batch)} threads)...")

        tasks = [run_agent_step_async(thread) for thread in batch]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        extractions.extend(batch_results)

        if i + BATCH_SIZE < len(new_threads):
            logger.info(f"[Watcher] Sleeping {DELAY_BETWEEN_BATCHES}s for rate limits...")
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    # Build result entries
    existing_results = _load_results()
    new_result_count = 0

    for thread, extraction in zip(new_threads, extractions):
        thread_id = thread.get("threadId", "")

        if isinstance(extraction, Exception):
            logger.error(f"[Watcher] Failed thread {thread_id}: {extraction}")
            continue

        if isinstance(extraction, dict):
            extracted = extraction
        elif isinstance(extraction, str):
            extracted = {"raw_output": extraction, "parsed_output": {}}
        else:
            extracted = {"raw_output": str(extraction), "parsed_output": {}}

        valid_thread = bool(extracted.get("valid_thread")) if isinstance(extracted, dict) else False
        if isinstance(extracted, dict):
            extracted.pop("valid_thread", None)

        entry = {
            "account_email": WATCHED_EMAIL,
            "thread_id": thread_id,
            "valid_thread": valid_thread,
            "messages": thread.get("messages", []),
            "extracted": extracted,
            "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        existing_results.append(entry)
        processed_ids.add(thread_id)
        new_result_count += 1

        if valid_thread:
            logger.info(f"✅ [Watcher] Valid staffing thread detected: {thread_id}")
        else:
            logger.info(f"⬜ [Watcher] Non-staffing thread: {thread_id}")

    # Persist
    _save_results(existing_results)
    _save_processed_ids(processed_ids)

    summary = {
        "status": "ok",
        "new_threads": new_result_count,
        "total_threads": len(threads),
        "total_extracted": len(existing_results),
    }
    logger.info(f"📊 [Watcher] Cycle complete: {summary}")
    return summary


async def watcher_loop() -> None:
    """
    Infinite loop that polls the inbox at POLL_INTERVAL_SECONDS intervals.
    Designed to run as a background task inside FastAPI's lifespan.
    """
    logger.info(f"🚀 [Watcher] Starting continuous inbox monitor for {WATCHED_EMAIL}")
    logger.info(f"   Poll interval: {POLL_INTERVAL_SECONDS}s | Results dir: {RESULTS_DIR}")

    # Small initial delay so the server finishes booting
    await asyncio.sleep(5)

    while True:
        try:
            await _poll_once()
        except Exception as e:
            logger.error(f"[Watcher] Unexpected error in poll cycle: {e}")

        logger.info(f"[Watcher] Sleeping {POLL_INTERVAL_SECONDS}s until next poll...")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
