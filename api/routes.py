# api/routes.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json
import logging
import datetime
import asyncio

from agent.tools import get_threads_for_date, get_threads_for_date_with_service, build_gmail_service_from_refresh_token
from agent.token_store import (
    delete_by_email,
    get_refresh_token_by_email,
    list_emails,
    read_token_json_by_email,
    upsert_token,
)
from agent.agent_runner import run_agent_step_async

logger = logging.getLogger("api.routes")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

router = APIRouter(prefix="/api", tags=["Email Extraction"])

# ────────────────────────────────
# 🔐 Token Ownership Verification
# ────────────────────────────────
def _canonicalize_email_for_compare(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    local = local.strip().lower()
    domain = domain.strip().lower()

    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0]
        local = local.replace(".", "")
        domain = "gmail.com"

    return f"{local}@{domain}"

def _verify_refresh_token_belongs_to_email(
    refresh_token: str, claimed_email: str, token_json: Optional[Dict[str, Any]] = None
) -> None:
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise HTTPException(status_code=400, detail="token_json.refresh_token is required")
    if not isinstance(claimed_email, str) or not claimed_email.strip():
        raise HTTPException(status_code=400, detail="email is required")

    try:
        gmail = build_gmail_service_from_refresh_token(refresh_token.strip(), token_json=token_json)
        profile = gmail.users().getProfile(userId="me").execute() or {}
        actual_email = str(profile.get("emailAddress", "") or "").strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Unable to validate token ownership: {e}")

    if not actual_email:
        raise HTTPException(status_code=400, detail="Unable to validate token ownership: missing profile emailAddress")

    if _canonicalize_email_for_compare(actual_email) != _canonicalize_email_for_compare(claimed_email):
        raise HTTPException(
            status_code=400,
            detail=f"Token belongs to '{actual_email}' but request email is '{claimed_email}'. Re-auth with the correct inbox.",
        )

# ────────────────────────────────
# 📅 Request Model
# ────────────────────────────────
class AccountUpsertRequest(BaseModel):
    email: str
    token_json: Dict[str, Any]
    label: str = ""

class AccountDeleteRequest(BaseModel):
    email: str

class DateRequest(BaseModel):
    date: str
    accounts: Optional[List[str]] = None
    
# ────────────────────────────────
# 🚀 Endpoint: Extract Emails (CHUNKED PARALLEL)
# ────────────────────────────────
@router.post("/extract_emails")
async def extract_emails(req: DateRequest) -> Dict[str, Any]:
    try:
        process_date = datetime.date.fromisoformat((req.date or "").strip())
    except Exception:
        raise HTTPException(status_code=422, detail='Invalid date format. Use "YYYY-MM-DD".')

    iso_date = process_date.isoformat()
    
    # --- RATE LIMIT CONTROLS ---
    BATCH_SIZE = 3             # How many threads to process at the exact same time
    DELAY_BETWEEN_BATCHES = 5.0  # Seconds to pause before firing the next batch

    try:
        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []

        # ----------------------------------------
        # Multi-account path
        # ----------------------------------------
        if req.accounts:
            for email in req.accounts:
                try:
                    token_data = read_token_json_by_email(email) or {}
                    refresh_token = token_data.get("refresh_token")
                    if not isinstance(refresh_token, str) or not refresh_token.strip():
                        raise RuntimeError(f"Missing refresh_token for email={email}")

                    gmail = build_gmail_service_from_refresh_token(refresh_token.strip(), token_json=token_data)
                    threads: List[Dict[str, Any]] = get_threads_for_date_with_service(gmail, iso_date)

                    logger.info(f"Processing {len(threads)} threads for {email} on {iso_date} in chunks of {BATCH_SIZE}...")
                    
                    extractions = []
                    # CHUNKED EXECUTION LOOP
                    for i in range(0, len(threads), BATCH_SIZE):
                        batch = threads[i:i + BATCH_SIZE]
                        logger.info(f"[{email}] Firing batch {i//BATCH_SIZE + 1} ({len(batch)} threads)...")
                        
                        tasks = [run_agent_step_async(thread) for thread in batch]
                        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                        extractions.extend(batch_results)
                        
                        if i + BATCH_SIZE < len(threads):
                            logger.info(f"[{email}] Sleeping {DELAY_BETWEEN_BATCHES}s to respect API rate limits...")
                            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

                    for thread, extraction in zip(threads, extractions):
                        if isinstance(extraction, Exception):
                            logger.error(f"Failed to process thread {thread.get('threadId')} after retries: {extraction}")
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

                        results.append(
                            {
                                "account_email": email,
                                "thread_id": thread.get("threadId"),
                                "valid_thread": valid_thread,
                                "messages": thread.get("messages", []),
                                "extracted": extracted,
                            }
                        )
                except Exception as e:
                    errors.append({"account_email": email, "error": str(e)})

            resp: Dict[str, Any] = {"date": iso_date, "results": results}
            if errors:
                resp["errors"] = errors
            if not results and not errors:
                resp["message"] = "No threads found"
            return resp

        # ----------------------------------------
        # Single-account path
        # ----------------------------------------
        threads: List[Dict[str, Any]] = get_threads_for_date(iso_date)
        if not threads:
            logger.info(f"No valid threads found for {iso_date}")
            return {"date": iso_date, "results": [], "message": "No threads found"}

        logger.info(f"Processing {len(threads)} threads for {iso_date} in chunks of {BATCH_SIZE}...")

        extractions = []
        # CHUNKED EXECUTION LOOP
        for i in range(0, len(threads), BATCH_SIZE):
            batch = threads[i:i + BATCH_SIZE]
            logger.info(f"Firing batch {i//BATCH_SIZE + 1} ({len(batch)} threads)...")
            
            tasks = [run_agent_step_async(thread) for thread in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            extractions.extend(batch_results)
            
            if i + BATCH_SIZE < len(threads):
                logger.info(f"Sleeping {DELAY_BETWEEN_BATCHES}s to respect API rate limits...")
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        for thread, extraction in zip(threads, extractions):
            if isinstance(extraction, Exception):
                logger.error(f"Failed to process thread {thread.get('threadId')} after retries: {extraction}")
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

            results.append(
                {
                    "thread_id": thread.get("threadId"),
                    "valid_thread": valid_thread,
                    "messages": thread.get("messages", []),
                    "extracted": extracted,
                }
            )

        return {"date": iso_date, "results": results}

    except Exception as e:
        logger.error(f"Error during email extraction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounts_health")
async def accounts_health() -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for email in list_emails():
        token_data = read_token_json_by_email(email)
        refresh_token = token_data.get("refresh_token") if isinstance(token_data, dict) else None
        status = "healthy" if isinstance(refresh_token, str) and refresh_token.strip() else "bad"
        results.append(
            {
                "account_email": email,
                "status": status,
                "stored_status": (token_data or {}).get("status", "") if isinstance(token_data, dict) else "",
                "updated_at": (token_data or {}).get("updated_at", "") if isinstance(token_data, dict) else "",
            }
        )
    return {"accounts": results}

@router.post("/accounts")
async def add_account(req: AccountUpsertRequest) -> Dict[str, Any]:
    token = req.token_json.get("refresh_token")
    _verify_refresh_token_belongs_to_email(token, req.email, token_json=req.token_json)
    token_file = upsert_token(email=req.email, token_json=req.token_json, label=req.label)
    return {"ok": True, "account_email": req.email, "token_file": token_file}

@router.put("/accounts")
async def update_account(req: AccountUpsertRequest) -> Dict[str, Any]:
    token = req.token_json.get("refresh_token")
    _verify_refresh_token_belongs_to_email(token, req.email, token_json=req.token_json)
    token_file = upsert_token(email=req.email, token_json=req.token_json, label=req.label)
    return {"ok": True, "account_email": req.email, "token_file": token_file}

@router.delete("/accounts")
async def remove_account(req: AccountDeleteRequest) -> Dict[str, Any]:
    deleted = delete_by_email(req.email)
    return {"ok": True, "account_email": req.email, "deleted": deleted}