# agent/agent_runner.py
import json
import logging
import re
import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, Any, List, Optional

# --- NEW: Tenacity for self-healing retries ---
from tenacity import retry, stop_after_attempt, wait_exponential

# --- NEW: Pydantic for strict JSON Schema enforcement ---
from pydantic import BaseModel, Field

from langchain.prompts import ChatPromptTemplate
from .llm import get_llm
from .body_cleaner import populate_parsed_body_for_thread_messages_async

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logger = logging.getLogger("agent.runner")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Utilities (100% untouched - your logic remains perfect)
# ---------------------------------------------------------------------------
def _safe_parse_date(d: str) -> datetime:
    """Parse RFC-2822/loose email date strings safely; fall back to epoch if needed."""
    try:
        return parsedate_to_datetime(d)
    except Exception:
        try:
            return datetime.fromisoformat(d)
        except Exception:
            return datetime.fromtimestamp(0)


_QUOTE_SPLIT_RE = re.compile(r"^\s*On .+? wrote:\s*$", flags=re.IGNORECASE | re.MULTILINE)

_EMPLOYEE_ID_PHONE_PATTERN = re.compile(
    r"^(.+?)\s+([A-Za-z]?\d{5,}(?:\s+\d{10})?|\d{10})$"
)

_EMPLOYEE_ID_PATTERN = re.compile(
    r"^(.+?)\s*([-–—]\s*\d+|\(\s*[A-Za-z]*\d+\s*\)|#\s*\d+|\s+[A-Za-z]?\d{4,})(\s+\d{10})?$"
)


def _title_case_preserve_id(name: str) -> str:
    """
    Convert a name to Title Case while preserving any employee ID/number and phone.
    Handles format: [ID] [Name] [Phone] where ID is at the beginning.
    """
    if not isinstance(name, str):
        return ""
    name = name.strip()
    if not name:
        return ""
    
    parts = name.split()
    if not parts:
        return ""

    first = re.sub(r"[^\w]", "", parts[0])
    has_prefix_id = bool(re.match(r'^[A-Za-z]?\d{5,}$', first))

    last = re.sub(r"\D", "", parts[-1]) if len(parts) > 1 else ""
    has_phone = bool(re.match(r'^\d{10}$', last))
    
    if has_prefix_id:
        id_part = first
        if has_phone and len(parts) > 2:
            name_parts = [re.sub(r"[^\w'-]", "", p) for p in parts[1:-1]]
            phone_part = last
            name_str = " ".join(name_parts).title()
            return f"{id_part} {name_str} {phone_part}"
        else:
            name_parts = [re.sub(r"[^\w'-]", "", p) for p in parts[1:]]
            name_str = " ".join(name_parts).title()
            return f"{id_part} {name_str}"
    elif has_phone and len(parts) > 1:
        name_parts = [re.sub(r"[^\w'-]", "", p) for p in parts[:-1]]
        phone_part = last
        name_str = " ".join(name_parts).title()
        return f"{name_str} {phone_part}"
    else:
        return name.title()


def _name_key_for_match_global(value: str) -> str:
    s = re.sub(r"\s+", " ", (value or "").strip().lower())
    s = re.sub(r"\b(?:n/?a|na)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b[a-z]?\d{4,}\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d{10}\b", " ", s)
    s = re.sub(r"[^a-z\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_SUPERVISOR_HINT_RE = re.compile(
    r"\b(supervisor|reporting supervisor|site supervisor|project supervisor|team lead|tl\b|manager)\b",
    flags=re.IGNORECASE,
)


def _extract_supervisor_name_keys_from_text(text: str) -> set[str]:
    keys: set[str] = set()
    if not isinstance(text, str) or not text.strip():
        return keys

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not _SUPERVISOR_HINT_RE.search(line):
            continue
        cleaned = re.sub(r"<[^>]+>", " ", line)
        cleaned = re.sub(r"\b\d{3}[-\s]?\d{3}[-\s]?\d{4}\b", " ", cleaned)
        cleaned = re.sub(r"\b\d{10}\b", " ", cleaned)
        cleaned = re.sub(r"\b(supervisor|reporting supervisor|site supervisor|project supervisor|team lead|manager|tl)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(will be|is|are|the|for|shift|site|on|at|cell|phone)\b", " ", cleaned, flags=re.IGNORECASE)
        for m in re.finditer(r"\b([A-Za-z][A-Za-z'\.-]*(?:\s+[A-Za-z][A-Za-z'\.-]*){0,3})\b", cleaned):
            key = _name_key_for_match_global(m.group(1))
            if key and len(key.split()) <= 4:
                keys.add(key)
    return keys


def _normalize_shift_date(value: str) -> str:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return raw

    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", raw, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y",
        "%d %B %Y", "%b %d %Y", "%B %d %Y",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue

    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.date().isoformat()
    except Exception:
        return value


def _clean_body(body: str) -> str:
    if not body:
        return ""
    parts = _QUOTE_SPLIT_RE.split(body)
    body = parts[0] if parts else body
    body = re.sub(r"\[cid:[^\]]+\]", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body

_HEADER_LINE_RE = re.compile(r"^\s*(from|to|cc|bcc|subject|sent|date)\s*:\s*.*$", flags=re.IGNORECASE)


def _strip_email_header_lines(text: str) -> str:
    if not text:
        return ""
    kept: List[str] = []
    for line in str(text).splitlines():
        if _HEADER_LINE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _build_body_corpus_for_matching(messages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        parsed_body = str(m.get("parsed_body") or "")
        body = str(m.get("body") or "")
        combined = (parsed_body + "\n" + body).strip()
        combined = _strip_email_header_lines(combined)
        combined = re.sub(r"\s+", " ", combined).strip().lower()
        if combined:
            parts.append(combined)
    return " ".join(parts).strip()


def _filter_names_to_body_mentions(names: List[str], body_corpus: str) -> List[str]:
    if not isinstance(names, list):
        return []
    corpus = re.sub(r"\s+", " ", str(body_corpus or "").strip().lower())
    if not corpus:
        return [n for n in names if isinstance(n, str) and n.strip()]

    corpus_tokens = set(re.findall(r"[a-z]+", corpus))

    def _extract_name_part(full_name: str) -> str:
        s = re.sub(r"\s+", " ", full_name.strip().lower())
        s = re.sub(r"\s+\d{10}\s*$", "", s)
        s = re.sub(r"\s*[-–—#]?\s*[a-z]*\d{4,}\s*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*\([a-z]*\d+\)\s*$", "", s, flags=re.IGNORECASE)
        return s.strip()

    def _normalize_name_for_match(name: str) -> str:
        s = re.sub(r"\s+", " ", (name or "").strip().lower())
        s = re.sub(r"\b(?:n/?a|na)\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\(\s*[a-z]*\d+\s*\)", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b[a-z]*\d{4,}\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"[^a-z\s]", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def _fuzzy_token_match(name_str: str, corpus_tokens: set, corpus: str) -> bool:
        if not name_str:
            return False
        if name_str in corpus:
            return True
        name_tokens = [t for t in re.findall(r"[a-z]+", name_str.lower()) if len(t) >= 2]
        if not name_tokens:
            return False
        matched_count = 0
        for token in name_tokens:
            if token in corpus_tokens:
                matched_count += 1
            else:
                if len(token) >= 4:
                    token_prefix = token[:4]
                    for ct in corpus_tokens:
                        if len(ct) >= 4 and (ct.startswith(token_prefix) or token.startswith(ct[:4])):
                            matched_count += 1
                            break
        if len(name_tokens) <= 2:
            return matched_count >= len(name_tokens)
        return matched_count >= len(name_tokens) * 0.7

    kept: List[str] = []
    seen: set[str] = set()
    for nm in names:
        if not isinstance(nm, str):
            continue
        full_needle = re.sub(r"\s+", " ", nm.strip().lower())
        if not full_needle:
            continue
        if full_needle in corpus:
            if full_needle not in seen:
                seen.add(full_needle)
                kept.append(nm)
            continue
        name_only = _normalize_name_for_match(_extract_name_part(nm) or nm)
        if name_only:
            if name_only in corpus:
                if full_needle not in seen:
                    seen.add(full_needle)
                    kept.append(nm)
                continue
            if _fuzzy_token_match(name_only, corpus_tokens, corpus):
                if full_needle not in seen:
                    seen.add(full_needle)
                    kept.append(nm)
    return kept


def _format_thread_for_prompt(thread: Dict[str, Any]) -> str:
    msgs: List[Dict[str, Any]] = thread.get("messages", [])
    msgs_sorted = sorted(msgs, key=lambda m: _safe_parse_date(m.get("date", "")))

    lines = []
    for idx, m in enumerate(msgs_sorted, start=1):
        lines.append(f"--- MESSAGE {idx} ---")
        lines.append(f"message_id: {m.get('id','')}")
        lines.append(f"from: {m.get('from','')}")
        lines.append(f"to: {m.get('to','')}")
        if m.get("cc"):
            lines.append(f"cc: {m.get('cc')}")
        lines.append(f"subject: {m.get('subject','')}")
        lines.append(f"date: {m.get('date','')}")
        lines.append("")
        body = m.get("body", "") or ""
        parsed_body = m.get("parsed_body", "") or ""
        lines.append(_clean_body(parsed_body if parsed_body else body))
        lines.append("")
    return "\n".join(lines).strip()

# ---------------------------------------------------------------------------
# 🏗️ Pydantic Schemas (Strict JSON Enforcement)
# ---------------------------------------------------------------------------
class EmployeeMention(BaseModel):
    message_id: str = Field(description="The ID of the message")
    names: List[str] = Field(description="List of employee names mentioned")

class Requirement(BaseModel):
    is_staffing: Optional[bool] = Field(default=False)
    req_key: Optional[str] = Field(default="")
    shift_date: Optional[str] = Field(default="")
    shift_time: Optional[str] = Field(default="")
    shift_hours: Optional[int] = Field(default=8)
    location_name: Optional[str] = Field(default="")
    client_id: Optional[str] = Field(default="")
    finalized_employees: Optional[List[str]] = Field(default_factory=list)
    all_employee_mentions: Optional[List[EmployeeMention]] = Field(default_factory=list)
    valid_email: Optional[bool] = Field(default=False)
    status: Optional[str] = Field(default="")
    raw_requirements: Optional[str] = Field(default="")

class ExtractionResult(BaseModel):
    threadId: str
    shift_date: Optional[str] = Field(default="")
    shift_time: Optional[str] = Field(default="")
    shift_hours: Optional[int] = Field(default=8)
    location_name: Optional[str] = Field(default="")
    client_id: Optional[str] = Field(default="")
    finalized_employees: Optional[List[str]] = Field(default_factory=list)
    all_employee_mentions: Optional[List[EmployeeMention]] = Field(default_factory=list)
    valid_thread: Optional[bool] = Field(default=False)
    status: Optional[str] = Field(default="")
    Requirements: Optional[List[Requirement]] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# Precompiled prompt & chain
# ---------------------------------------------------------------------------
PROMPT_TEMPLATE = ChatPromptTemplate.from_template(
    """
You are an intelligent staffing email parser.

Below is an email thread between clients and the QStaff scheduling team about shift staffing.
Return a single VALID JSON object ONLY (no extra text).

ThreadId: {thread_id}

Thread (oldest → newest):
{thread_text}

Your job:
1) Extract shift details (date/time/hours/location/client code).
2) Extract employee NAMES mentioned in the thread (not sender/receiver names).
3) Determine the **finalized** list of employees scheduled, taking into account changes across the conversation.

Critical anti-hallucination rule:
- NEVER extract employee names from email header metadata, including any lines that look like
  "From:", "To:", "Cc:", "Bcc:", "Subject:", "Sent:", or "Date:" (even if they appear inside forwarded/quoted blocks).
  Only extract worker names from the scheduling content in the message body.
- NEVER treat recipient/address lists as employees. Specifically, DO NOT include any person in finalized_employees
  if they are only mentioned in recipient metadata or email-address lists such as:
  "Name <name@domain.com>", "name@domain.com", or comma-separated recipient lists.
  Include a person only when they are part of the staffing/scheduling content (e.g., listed for a shift, confirmed, scheduled).
- Supervisors/managers/team leads are NOT employees for staffing. Do NOT include names that appear only as
  "Supervisor:", "Reporting Supervisor:", "Site supervisor", "Project supervisor", "Manager", or "TL/Team Lead"
  in finalized_employees or all_employee_mentions, unless the email explicitly schedules them as an associate.
- all_employee_mentions must contain only associate/worker names from staffing content; never include supervisor-only names.

Use this logic for finalization:
- Prefer the **latest** message that indicates confirmation/approval/affirmation, e.g. lines like:
  "She is confirmed", "He is confirmed", "They are confirmed", "Confirmed list",
  "Please confirm the updated list", "Final list", "Schedule X/Y/Z",
  "We will have X/Y/Z", "Here is what we have".
- **CRITICAL: Only finalize names if the latest confirmation message comes from *@qstaff.ca (any account with qstaff.ca domain).** If the latest confirmation is not from qstaff.ca, do not populate finalized_employees (leave it empty).
- If there are cancellations or replacements later ("X won't be able to come",
  "sending his replacement Y"), **remove** canceled names and **include** replacements.
- If no explicit confirmation from qstaff.ca exists, leave finalized_employees empty.
- The names must be human worker names like "Mansi Dhanani", "Aaryan Rawal".
  Ignore company names or role titles.
- **CRITICAL - Employee IDs and Phone Numbers MUST be included**: When the email contains an employee ID or SQ ID 
  (patterns like "T244264", "T243457", "153257", "N/A") and a phone number, you MUST include both.
  
  Email format examples and expected output (keep same format as email):
  - "T244264 Harmandeep singh dhindsa 4377558679" → "T244264 Harmandeep Singh Dhindsa 4377558679"
  - "T243457 Dev Ray 2262605361" → "T243457 Dev Ray 2262605361"
  - "N/A Riddhi arora 2262601134" → "Riddhi Arora 2262601134" (N/A means no ID, so omit it)
  - "153257 Beant Kaur Sidhu 6475942588" → "153257 Beant Kaur Sidhu 6475942588"
  
  The format is: [SQ ID] [Name] [Phone Number]. 
  Output format should be: [SQ ID if present] [Name] [Phone Number if present]
  
- CRITICAL: Preserve the EXACT spelling of names as they appear in the email body.
  Do NOT correct typos, spelling mistakes, or modify name spellings in any way.
  If the email says "dhindsa", output "Dhindsa" (only capitalize, don't change letters).
  If the email says "Chuahan", output "Chuahan" (not "Chauhan").
- Keep names in Title Case (but preserve the original spelling, ID, and phone format as-is).
- Deduplicate the final list.

Map the client/company ID heuristically from context (subject/from/body):
- If it's Q2 Management context, set "client_id" to "Q2".
- Otherwise choose one of: "TFT", "SQ", "VAS" if those appear clearly. If unknown, put "".

Cancellation / status logic:
- If the thread clearly cancels a shift or booking for the extracted shift_date
  (e.g. "please cancel", "we don't need them anymore", "do not send anyone"),
  set "status": "delete".
- Otherwise, set "status": "" (empty string).
- Even when status = "delete", still extract shift_date and finalized_employees normally.

Always include a top-level "Requirements" ARRAY.
- If the thread has ONE staffing requirement, "Requirements" must contain exactly 1 item.
- If the thread has multiple distinct staffing requirements (e.g., Day + Night shifts, different locations, different dates),
  "Requirements" must contain one item per distinct requirement and you MUST NOT collapse them.

Additional rules for Requirements:
- req_key MUST be present for each requirement item and must be computed from that item's own fields.
- If later messages update staffing for an existing requirement, UPDATE the existing requirement (same req_key).
  Do NOT create a new requirement item for an update.
- Only create a new requirement item when it is truly distinct (different shift_date and/or shift_time and/or location_name).
- is_staffing must be true ONLY for emails about scheduling/confirming/canceling staffing/shifts.
- If is_staffing is false, do not put names in finalized_employees (keep them in mentions if needed).
  Examples of NOT staffing (set is_staffing=false, valid_email=false): training status, employees late, employee sent home,
  illness/incident reports, compliance reminders.

Rules:
- Ignore internal operational boilerplate/signatures.
- If irrelevant to staffing, set valid_email=false and keep other fields minimal.
- You MUST NOT guess or infer location_name or client_id; if they are not clearly and
  explicitly stated in the emails, set them to "".
- Missing values are better than wrong values. If you are unsure, leave the field empty
  or set valid_email=false.
""",
    template_format="f-string",
)

# ---------------------------------------------------------------------------
# 🔗 UPDATED: Structured LLM Chain
# ---------------------------------------------------------------------------
LLM_INSTANCE = get_llm()
STRUCTURED_LLM = LLM_INSTANCE.with_structured_output(ExtractionResult)
CHAIN = PROMPT_TEMPLATE | STRUCTURED_LLM

# ---------------------------------------------------------------------------
# UPDATED: Async Main runner with Tenacity Retries & Pydantic Parsing
# ---------------------------------------------------------------------------
@retry(
    stop=stop_after_attempt(3), 
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
async def run_agent_step_async(email_thread: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a structured extraction pass asynchronously.
    Wrapped with tenacity @retry to auto-recover from Groq API hiccups.
    """
    thread_id = str(email_thread.get("threadId", ""))
    logger.info(f"Processing thread {thread_id} ...")

    # 1. Clean bodies in parallel using our new async cleaner
    try:
        await populate_parsed_body_for_thread_messages_async(email_thread.get("messages", []) or [])
    except Exception as e:
        logger.warning(f"Thread {thread_id}: Body cleaning failed, proceeding with raw bodies. Error: {e}")

    thread_text = _format_thread_for_prompt(email_thread)

    # 2. Extract strictly validated JSON via Pydantic
    response = await CHAIN.ainvoke({"thread_text": thread_text, "thread_id": thread_id})

    # 3. Convert the Pydantic object straight back into a dictionary! No string parsing needed!
    parsed = response.model_dump()
    raw_output = json.dumps(parsed, ensure_ascii=False)

    # Minimal normalization on parsed_output (if any)
    if isinstance(parsed, dict):
        body_corpus = _build_body_corpus_for_matching(email_thread.get("messages", []) or [])
        if "threadId" not in parsed:
            parsed["threadId"] = thread_id
        parsed.setdefault("shift_date", "")
        parsed.setdefault("shift_time", "")
        parsed.setdefault("shift_hours", 8)
        parsed.setdefault("location_name", "")
        parsed.setdefault("client_id", "")
        parsed.setdefault("status", "")

        # Normalize shift_date to ISO format when possible
        parsed["shift_date"] = _normalize_shift_date(parsed.get("shift_date", ""))

        # Thread-level validity field is exposed as `valid_thread` in parsed_output.
        # Accept legacy model outputs that may still use `valid_email`.
        parsed.setdefault("valid_thread", False)
        if "valid_thread" not in parsed and "valid_email" in parsed:
            parsed["valid_thread"] = bool(parsed.get("valid_email"))
        if not parsed.get("shift_date"):
            parsed["valid_email"] = False

        if not isinstance(parsed.get("finalized_employees"), list):
            parsed["finalized_employees"] = []
        if not isinstance(parsed.get("all_employee_mentions"), list):
            parsed["all_employee_mentions"] = []

        # Coerce names into Title Case (preserving employee IDs/numbers), dedupe while preserving order
        seen = set()
        normalized_final: List[str] = []
        for name in parsed.get("finalized_employees", []):
            if not isinstance(name, str):
                continue
            clean = re.sub(r"\s+", " ", name.strip())
            # Preserve employee IDs: split name from ID, title-case just the name part
            clean = _title_case_preserve_id(clean)
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                normalized_final.append(clean)
        parsed["finalized_employees"] = normalized_final
        parsed["finalized_employees"] = _filter_names_to_body_mentions(parsed["finalized_employees"], body_corpus)

        # Normalize all_employee_mentions entries
        mentions_norm = []
        for ev in parsed.get("all_employee_mentions", []):
            if not isinstance(ev, dict):
                continue
            msg_id = str(ev.get("message_id", ""))
            names = ev.get("names", [])
            fixed_names = []
            for nm in names or []:
                if isinstance(nm, str):
                    nm2 = re.sub(r"\s+", " ", nm.strip())
                    # Preserve employee IDs: split name from ID, title-case just the name part
                    nm2 = _title_case_preserve_id(nm2)
                    if nm2:
                        fixed_names.append(nm2)
            fixed_names = _filter_names_to_body_mentions(fixed_names, body_corpus)
            mentions_norm.append({"message_id": msg_id, "names": fixed_names})
        parsed["all_employee_mentions"] = mentions_norm


        # -------------------------------------------------------------------
        # Requirements normalization (only if LLM provided it)
        # -------------------------------------------------------------------
        def _normalize_name_list(value: Any) -> List[str]:
            if not isinstance(value, list):
                return []
            seen_local = set()
            normalized: List[str] = []
            for nm in value:
                if not isinstance(nm, str):
                    continue
                clean = re.sub(r"\s+", " ", nm.strip())
                # Preserve employee IDs: split name from ID, title-case just the name part
                clean = _title_case_preserve_id(clean)
                key = clean.lower()
                if clean and key not in seen_local:
                    seen_local.add(key)
                    normalized.append(clean)
            return normalized

        def _normalize_mentions_list(value: Any) -> List[Dict[str, Any]]:
            if not isinstance(value, list):
                return []
            out: List[Dict[str, Any]] = []
            for ev in value:
                if not isinstance(ev, dict):
                    continue
                msg_id = str(ev.get("message_id", ""))
                out.append({"message_id": msg_id, "names": _normalize_name_list(ev.get("names", []))})
            return out

        def _infer_valid_requirement(payload: Dict[str, Any]) -> bool:
            status_val = str(payload.get("status", "") or "").strip().lower()
            if status_val == "delete":
                return True
            if str(payload.get("shift_date", "") or "").strip():
                return True
            if str(payload.get("shift_time", "") or "").strip():
                return True
            if str(payload.get("location_name", "") or "").strip():
                return True
            if str(payload.get("client_id", "") or "").strip():
                return True
            return False

        def _compute_req_key(payload: Dict[str, Any]) -> str:
            sd = str(payload.get("shift_date", "") or "").strip()
            st = str(payload.get("shift_time", "") or "").strip()
            loc = str(payload.get("location_name", "") or "").strip()
            return f"{sd}|{st}|{loc}".lower()

        def _normalize_bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                v = value.strip().lower()
                if v in {"true", "t", "yes", "y", "1"}:
                    return True
                if v in {"false", "f", "no", "n", "0"}:
                    return False
            return False

        def _infer_is_staffing_from_thread(thread_text: str) -> bool:
            t = (thread_text or "").lower()
            return any(
                s in t
                for s in [
                    "please schedule",
                    "schedule staffing",
                    "schedule staff",
                    "staffing request",
                    "please confirm",
                    "confirm names",
                    "send names",
                    "send the names",
                    "cancel this request",
                    "please cancel",
                    "shift timing",
                ]
            )

        def _has_staffing_action_signal(thread_text: str) -> bool:
            return _infer_is_staffing_from_thread(thread_text)

        def _has_sufficient_shift_details(payload: Dict[str, Any]) -> bool:
            present = 0
            if str(payload.get("shift_date", "") or "").strip():
                present += 1
            if str(payload.get("shift_time", "") or "").strip():
                present += 1
            if str(payload.get("location_name", "") or "").strip():
                present += 1
            return present >= 2

        def _has_any_shift_detail(payload: Dict[str, Any]) -> bool:
            return bool(
                str(payload.get("shift_date", "") or "").strip()
                or str(payload.get("shift_time", "") or "").strip()
                or str(payload.get("location_name", "") or "").strip()
            )

        requirements_obj = parsed.get("Requirements")
        requirements_items: List[Dict[str, Any]] = []
        normalized_requirements: List[Dict[str, Any]] = []
        if isinstance(requirements_obj, dict) and requirements_obj:
            for k in sorted(requirements_obj.keys(), key=lambda v: int(re.search(r"(\d+)", str(v)).group(1)) if re.search(r"(\d+)", str(v)) else 0):
                v = requirements_obj.get(k)
                if not isinstance(v, dict):
                    continue
                v = dict(v)
                v["_raw_output"] = json.dumps(v, ensure_ascii=False)
                requirements_items.append(v)
        elif isinstance(requirements_obj, list) and requirements_obj:
            for v in requirements_obj:
                if not isinstance(v, dict):
                    continue
                v = dict(v)
                raw_obj = {k: val for k, val in v.items() if k not in {"raw_requirements", "raw_output"}}
                v["_raw_output"] = json.dumps(raw_obj, ensure_ascii=False)
                requirements_items.append(v)

        if requirements_items:
            msg_by_id: Dict[str, Dict[str, Any]] = {}
            for m in (email_thread.get("messages", []) or []):
                if isinstance(m, dict):
                    mid = str(m.get("id", ""))
                    if mid:
                        msg_by_id[mid] = m

            def _get_message_text(mid: str) -> str:
                m = msg_by_id.get(str(mid), {}) or {}
                body = (m.get("parsed_body") or m.get("body") or "")
                return _clean_body(str(body))

            def _get_message_corpus(mid: str) -> str:
                m = msg_by_id.get(str(mid), {}) or {}
                if not m:
                    return ""
                return _build_body_corpus_for_matching([m])

            def _infer_shift_time_from_text(text: str) -> str:
                t = (text or "").lower()

                if any(k in t for k in ["overnight", "night shift", "2nd shift", "second shift", "tonight"]):
                    return "Night"
                if any(k in t for k in ["afternoon shift", "this afternoon"]):
                    return "Afternoon"
                if any(k in t for k in ["morning shift", "this morning", "1st shift", "first shift"]):
                    return "Day"

                time_token = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
                range_re = re.compile(
                    rf"{time_token}\s*(?:-|to|–|—)\s*{time_token}",
                    flags=re.IGNORECASE,
                )

                def _to_hour(h: str, m: str | None, ap: str | None) -> int | None:
                    try:
                        hour = int(h)
                        minute = int(m) if m else 0
                    except Exception:
                        return None
                    ap_norm = (ap or "").lower().strip()
                    if ap_norm not in {"am", "pm"}:
                        return None
                    if hour == 12:
                        hour = 0
                    if ap_norm == "pm":
                        hour += 12
                    if not (0 <= minute <= 59) or not (0 <= hour <= 23):
                        return None
                    return hour

                m_match = range_re.search(text or "")
                if m_match:
                    sh, sm, sap, eh, em, eap = m_match.group(1), m_match.group(2), m_match.group(3), m_match.group(4), m_match.group(5), m_match.group(6)
                    start_hour = _to_hour(sh, sm, sap)
                    end_hour = _to_hour(eh, em, eap)
                    if start_hour is not None and end_hour is not None:
                        crosses_midnight = end_hour < start_hour
                        if crosses_midnight or start_hour >= 17 or "pm to" in t and "am" in t:
                            return "Night"
                        if 12 <= start_hour < 17:
                            return "Afternoon"
                        return "Day"

                single_time_re = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", flags=re.IGNORECASE)
                sm_match = single_time_re.search(text or "")
                if sm_match:
                    sh, smin, sap = sm_match.group(1), sm_match.group(2), sm_match.group(3)
                    start_hour = _to_hour(sh, smin, sap)
                    if start_hour is not None:
                        if start_hour >= 17 or start_hour < 5:
                            return "Night"
                        if 12 <= start_hour < 17:
                            return "Afternoon"
                        return "Day"

                return ""

            for req_val in requirements_items:
                if not isinstance(req_val, dict):
                    continue

                req_val.setdefault("shift_date", "")
                req_val.setdefault("shift_time", "")
                req_val.setdefault("shift_hours", 8)
                req_val.setdefault("location_name", "")
                req_val.setdefault("client_id", "")
                req_val.setdefault("status", "")

                req_val["shift_date"] = _normalize_shift_date(req_val.get("shift_date", ""))
                req_val["finalized_employees"] = _normalize_name_list(req_val.get("finalized_employees", []))
                req_val["all_employee_mentions"] = _normalize_mentions_list(req_val.get("all_employee_mentions", []))

                req_val["finalized_employees"] = _filter_names_to_body_mentions(req_val["finalized_employees"], body_corpus)
                if req_val.get("all_employee_mentions"):
                    pruned_mentions: List[Dict[str, Any]] = []
                    for ev in req_val.get("all_employee_mentions", []) or []:
                        if not isinstance(ev, dict):
                            continue
                        mid = str(ev.get("message_id", ""))
                        msg_corpus = _get_message_corpus(mid)
                        msg_text = _get_message_text(mid)
                        supervisor_keys = _extract_supervisor_name_keys_from_text(msg_text)
                        ev_names = _filter_names_to_body_mentions(
                            ev.get("names", []) or [],
                            msg_corpus or body_corpus,
                        )
                        if supervisor_keys:
                            ev_names = [
                                nm
                                for nm in ev_names
                                if _name_key_for_match_global(nm) not in supervisor_keys
                            ]
                        pruned_mentions.append({"message_id": mid, "names": ev_names})
                    req_val["all_employee_mentions"] = pruned_mentions

                thread_supervisor_keys: set[str] = set()
                qstaff_name_keys: set[str] = set()
                for m_msg in (email_thread.get("messages", []) or []):
                    if not isinstance(m_msg, dict):
                        continue
                    mid = str(m_msg.get("id", "")).strip()
                    m_text = _get_message_text(mid)
                    thread_supervisor_keys.update(_extract_supervisor_name_keys_from_text(m_text))
                    sender = str(m_msg.get("from") or m_msg.get("From") or "").lower()
                    sender_match = re.search(r"@([\w\.-]+)", sender)
                    if sender_match and sender_match.group(1).endswith("qstaff.ca"):
                        for ev in req_val.get("all_employee_mentions", []) or []:
                            if isinstance(ev, dict) and str(ev.get("message_id", "")).strip() == mid:
                                for nm in (ev.get("names", []) or []):
                                    qstaff_name_keys.add(_name_key_for_match_global(str(nm)))
                if thread_supervisor_keys:
                    req_val["finalized_employees"] = [
                        nm
                        for nm in (req_val.get("finalized_employees", []) or [])
                        if (
                            _name_key_for_match_global(nm) not in thread_supervisor_keys
                            or _name_key_for_match_global(nm) in qstaff_name_keys
                        )
                    ]

                if not str(req_val.get("shift_time", "") or "").strip():
                    candidate_ids = []
                    for ev in req_val.get("all_employee_mentions", []) or []:
                        if isinstance(ev, dict):
                            mid = str(ev.get("message_id", "")).strip()
                            if mid:
                                candidate_ids.append(mid)

                    req_names = [
                        n for n in (req_val.get("finalized_employees") or []) if isinstance(n, str) and n.strip()
                    ]

                    inferred = ""
                    for mid in candidate_ids:
                        msg_text = _get_message_text(mid)
                        if req_names:
                            txt_l = msg_text.lower()
                            if not any(n.strip().lower() in txt_l for n in req_names):
                                continue
                        inferred = _infer_shift_time_from_text(msg_text)
                        if inferred:
                            break
                    if not inferred:
                        if req_names:
                            best_mid = ""
                            best_hits = 0
                            for mid in msg_by_id.keys():
                                txt = _get_message_text(mid).lower()
                                hits = 0
                                for n in req_names:
                                    if n.strip().lower() in txt:
                                        hits += 1
                                if hits > best_hits:
                                    best_hits = hits
                                    best_mid = mid
                            if best_mid and best_hits > 0:
                                inferred = _infer_shift_time_from_text(_get_message_text(best_mid))
                    if not inferred:
                        for mid, m_msg in msg_by_id.items():
                            inferred = _infer_shift_time_from_text(_get_message_text(mid))
                            if inferred:
                                break

                    if inferred:
                        req_val["shift_time"] = inferred

                req_val["req_key"] = _compute_req_key(req_val)

                model_is_staffing = _normalize_bool(req_val.get("is_staffing", False))
                if str(req_val.get("status", "") or "").strip().lower() == "delete":
                    is_staffing = True
                else:
                    if "is_staffing" in req_val:
                        if model_is_staffing and not _has_staffing_action_signal(thread_text):
                            is_staffing = False
                        else:
                            is_staffing = model_is_staffing
                    else:
                        is_staffing = _infer_is_staffing_from_thread(thread_text)
                req_val["is_staffing"] = bool(is_staffing)

                if not isinstance(req_val.get("valid_email"), bool):
                    req_val["valid_email"] = False
                req_val["valid_email"] = bool(req_val.get("valid_email")) or _infer_valid_requirement(req_val)

                if req_val.get("valid_email") and not _has_sufficient_shift_details(req_val):
                    req_val["valid_email"] = False

                if req_val.get("valid_email") and not req_val.get("is_staffing"):
                    req_val["valid_email"] = False

                if not req_val.get("is_staffing"):
                    req_val["shift_date"] = ""
                    req_val["shift_time"] = ""
                    req_val["location_name"] = ""

                if not req_val.get("valid_email") and not _has_any_shift_detail(req_val):
                    req_val["finalized_employees"] = []

                if not req_val.get("is_staffing"):
                    req_val["finalized_employees"] = []

                req_val["req_key"] = _compute_req_key(req_val)

                req_val.pop("raw_output", None)
                req_val.pop("raw_requirements", None)
                req_val["raw_requirements"] = str(req_val.get("_raw_output", ""))
                req_val.pop("is_staffing", None)
                req_val.pop("_raw_output", None)
                normalized_requirements.append(req_val)

            def _is_qstaff_sender(from_field: str) -> bool:
                if not isinstance(from_field, str):
                    return False
                match = re.search(r"@([\w\.-]+)", from_field.lower())
                return bool(match and match.group(1).endswith("qstaff.ca"))

            def _message_text(msg: Dict[str, Any]) -> str:
                return _clean_body(str(msg.get("parsed_body") or msg.get("body") or ""))

            def _is_confirmation_text(text: str) -> bool:
                return bool(
                    re.search(
                        r"\b(please\s+confirm|confirm\s*[:-]|confirmed|confirm\s+the\s+below|qstaff\s+has\s+confirmed)\b",
                        text or "",
                        flags=re.IGNORECASE,
                    )
                )

            def _is_staffing_change_text(text: str) -> bool:
                return bool(
                    re.search(
                        r"\b(add|remove|cancel|not\s+available|replace|instead|only\s+need|need\s+total|schedule\s+total|please\s+schedule\s+total|change\s+to)\b",
                        text or "",
                        flags=re.IGNORECASE,
                    )
                )

            def _strip_ids_for_match(value: str) -> str:
                s = re.sub(r"\s+", " ", (value or "").strip().lower())
                s = re.sub(r"\b(?:n/?a)\b", " ", s, flags=re.IGNORECASE)
                s = re.sub(r"\b[a-z]?\d{4,}\b", " ", s, flags=re.IGNORECASE)
                s = re.sub(r"\b\d{10}\b", " ", s)
                s = re.sub(r"[^a-z\s]", " ", s)
                return re.sub(r"\s+", " ", s).strip()

            def _text_has_any_req_name(text: str, req_names: List[str]) -> bool:
                t_raw = re.sub(r"\s+", " ", (text or "").strip().lower())
                t_norm = _strip_ids_for_match(text or "")
                for nm in req_names or []:
                    if not isinstance(nm, str):
                        continue
                    n_raw = re.sub(r"\s+", " ", nm.strip().lower())
                    if n_raw and n_raw in t_raw:
                        return True
                    n_norm = _strip_ids_for_match(nm)
                    if n_norm and n_norm in t_norm:
                        return True
                return False

            def _name_compare_key(name: str) -> str:
                return _strip_ids_for_match(name or "")

            def _should_keep_finalized_employees(req: Dict[str, Any], messages: List[Dict[str, Any]]) -> bool:
                req_names = [n for n in (req.get("finalized_employees") or []) if isinstance(n, str) and n.strip()]
                if not req_names:
                    for ev in req.get("all_employee_mentions", []) or []:
                        if isinstance(ev, dict):
                            req_names.extend([x for x in (ev.get("names", []) or []) if isinstance(x, str) and x.strip()])
                    req_names = _normalize_name_list(req_names)
                if not req_names:
                    return False

                messages_sorted = sorted(
                    [m for m in (messages or []) if isinstance(m, dict)],
                    key=lambda m: _safe_parse_date(str(m.get("date", ""))),
                )
                if not messages_sorted:
                    return False

                mention_names_by_id: Dict[str, List[str]] = {}
                for ev in req.get("all_employee_mentions", []) or []:
                    if isinstance(ev, dict):
                        mid = str(ev.get("message_id", "")).strip()
                        if mid:
                            mention_names_by_id[mid] = [
                                x for x in (ev.get("names", []) or []) if isinstance(x, str) and x.strip()
                            ]

                def _is_qstaff_confirmation_with_names(msg: Dict[str, Any]) -> bool:
                    sender = str(msg.get("from") or msg.get("From") or "")
                    if not _is_qstaff_sender(sender):
                        return False
                    msg_id = str(msg.get("id", "")).strip()
                    text = _message_text(msg)
                    has_names = bool(mention_names_by_id.get(msg_id)) or _text_has_any_req_name(text, req_names)
                    if not has_names:
                        return False
                    return _is_confirmation_text(text) or bool(mention_names_by_id.get(msg_id))

                latest_qstaff_confirm_idx = -1
                for idx, msg in enumerate(messages_sorted):
                    if _is_qstaff_confirmation_with_names(msg):
                        latest_qstaff_confirm_idx = idx
                if latest_qstaff_confirm_idx < 0:
                    return False

                latest_change_idx = -1
                for idx in range(latest_qstaff_confirm_idx + 1, len(messages_sorted)):
                    msg = messages_sorted[idx]
                    sender = str(msg.get("from") or msg.get("From") or "")
                    if _is_qstaff_sender(sender):
                        continue
                    if _is_staffing_change_text(_message_text(msg)):
                        latest_change_idx = idx

                if latest_change_idx < 0:
                    return True

                for idx in range(latest_change_idx + 1, len(messages_sorted)):
                    if _is_qstaff_confirmation_with_names(messages_sorted[idx]):
                        return True
                return False

            def _latest_qstaff_confirmation_names(req: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[str]:
                req_names = [n for n in (req.get("finalized_employees") or []) if isinstance(n, str) and n.strip()]
                if not req_names:
                    for ev in req.get("all_employee_mentions", []) or []:
                        if isinstance(ev, dict):
                            req_names.extend([x for x in (ev.get("names", []) or []) if isinstance(x, str) and x.strip()])
                    req_names = _normalize_name_list(req_names)
                messages_sorted = sorted(
                    [m for m in (messages or []) if isinstance(m, dict)],
                    key=lambda m: _safe_parse_date(str(m.get("date", ""))),
                )
                mention_names_by_id: Dict[str, List[str]] = {}
                for ev in req.get("all_employee_mentions", []) or []:
                    if isinstance(ev, dict):
                        mid = str(ev.get("message_id", "")).strip()
                        if mid:
                            mention_names_by_id[mid] = [
                                x for x in (ev.get("names", []) or []) if isinstance(x, str) and x.strip()
                            ]

                latest_names: List[str] = []
                for msg in messages_sorted:
                    sender = str(msg.get("from") or msg.get("From") or "")
                    if not _is_qstaff_sender(sender):
                        continue
                    msg_id = str(msg.get("id", "")).strip()
                    text = _message_text(msg)
                    if _is_staffing_change_text(text):
                        continue
                    has_names = bool(mention_names_by_id.get(msg_id)) or _text_has_any_req_name(text, req_names)
                    if not has_names:
                        continue
                    if _is_confirmation_text(text) or bool(mention_names_by_id.get(msg_id)):
                        latest_names = mention_names_by_id.get(msg_id, []) or latest_names
                return latest_names

            def _apply_staffing_changes_to_finalized(req: Dict[str, Any], messages: List[Dict[str, Any]]) -> List[str]:
                final_names: List[str] = [
                    n for n in (req.get("finalized_employees") or []) if isinstance(n, str) and n.strip()
                ]
                if not final_names:
                    return []

                messages_sorted = sorted(
                    [m for m in (messages or []) if isinstance(m, dict)],
                    key=lambda m: _safe_parse_date(str(m.get("date", ""))),
                )
                mention_names_by_id: Dict[str, List[str]] = {}
                for ev in req.get("all_employee_mentions", []) or []:
                    if isinstance(ev, dict):
                        mid = str(ev.get("message_id", "")).strip()
                        if mid:
                            mention_names_by_id[mid] = [
                                x for x in (ev.get("names", []) or []) if isinstance(x, str) and x.strip()
                            ]

                def _remove_name(target: str) -> None:
                    k = _name_compare_key(target)
                    if not k:
                        return
                    nonlocal final_names
                    final_names = [n for n in final_names if _name_compare_key(n) != k]

                def _add_name(target: str) -> None:
                    k = _name_compare_key(target)
                    if not k:
                        return
                    if not any(_name_compare_key(n) == k for n in final_names):
                        final_names.append(target)

                def _names_mentioned_in_text_from_final(text: str) -> List[str]:
                    out: List[str] = []
                    t_norm = _strip_ids_for_match(text or "")
                    for nm in final_names:
                        k = _name_compare_key(nm)
                        if k and k in t_norm:
                            out.append(nm)
                    return out

                for msg in messages_sorted:
                    text = _message_text(msg)
                    low = (text or "").lower()
                    if not _is_staffing_change_text(text):
                        continue
                    msg_id = str(msg.get("id", "")).strip()
                    mentions = mention_names_by_id.get(msg_id, []) or []
                    if not mentions:
                        continue

                    has_replacement = bool(re.search(r"\b(replacement|replace|instead)\b", low, flags=re.IGNORECASE))
                    has_unavailable = bool(
                        re.search(
                            r"\b(not\s+available|won['’]?t\s+be\s+able|unable|can['’]?t|cannot|cancel|remove)\b",
                            low,
                            flags=re.IGNORECASE,
                        )
                    )

                    if has_replacement and has_unavailable and len(mentions) >= 2:
                        _remove_name(mentions[0])
                        for nm in mentions[1:]:
                            _add_name(nm)
                        continue

                    if has_replacement and has_unavailable and len(mentions) == 1:
                        replacement = mentions[0]
                        rep_key = _name_compare_key(replacement)
                        for old_nm in _names_mentioned_in_text_from_final(text):
                            if _name_compare_key(old_nm) != rep_key:
                                _remove_name(old_nm)
                        _add_name(replacement)
                        continue

                    if has_unavailable:
                        removed_any = False
                        for nm in mentions:
                            _remove_name(nm)
                            removed_any = True
                        if not removed_any:
                            for old_nm in _names_mentioned_in_text_from_final(text):
                                _remove_name(old_nm)
                        if has_replacement or re.search(r"\b(add|confirm)\b", low, flags=re.IGNORECASE):
                            for nm in mentions[1:]:
                                _add_name(nm)
                        continue

                    if has_replacement and len(mentions) >= 1:
                        for nm in mentions[-1:]:
                            _add_name(nm)

                return _normalize_name_list(final_names)

            def _thread_has_staffing_change(messages: List[Dict[str, Any]]) -> bool:
                for m_msg in (messages or []):
                    if not isinstance(m_msg, dict):
                        continue
                    if _is_staffing_change_text(_message_text(m_msg)):
                        return True
                return False

            def _stable_req_order(item: Dict[str, Any]) -> tuple:
                return (
                    str(item.get("shift_date") or ""),
                    str(item.get("shift_time") or ""),
                    str(item.get("location_name") or ""),
                    str(item.get("req_key") or ""),
                )

            normalized_requirements = sorted(normalized_requirements, key=_stable_req_order)

            for req in normalized_requirements:
                msgs = email_thread.get("messages", []) or []
                if not _should_keep_finalized_employees(req, msgs):
                    req["finalized_employees"] = []
                    continue
                current_names = _normalize_name_list(req.get("finalized_employees", []) or [])
                latest_confirmed_names = _latest_qstaff_confirmation_names(req, msgs)
                if latest_confirmed_names:
                    latest_norm = _normalize_name_list(latest_confirmed_names)
                    current_with_digits = sum(1 for n in current_names if re.search(r"\d", str(n)))
                    latest_with_digits = sum(1 for n in latest_norm if re.search(r"\d", str(n)))
                    
                    if (
                        current_names
                        and (
                            (_thread_has_staffing_change(msgs) and len(latest_norm) < len(current_names))
                            or (latest_with_digits < current_with_digits)
                        )
                    ):
                        merged = list(current_names)
                        seen_keys = {_name_compare_key(n) for n in merged}
                        for nm in latest_norm:
                            k = _name_compare_key(nm)
                            if k and k not in seen_keys:
                                merged.append(nm)
                                seen_keys.add(k)
                        req["finalized_employees"] = _normalize_name_list(merged)
                    else:
                        req["finalized_employees"] = latest_norm
                else:
                    req["finalized_employees"] = current_names
                req["finalized_employees"] = _apply_staffing_changes_to_finalized(req, msgs)

        for req in normalized_requirements:
            raw_obj = {
                "req_key": req.get("req_key", ""),
                "shift_date": req.get("shift_date", ""),
                "shift_time": req.get("shift_time", ""),
                "shift_hours": req.get("shift_hours", 8),
                "location_name": req.get("location_name", ""),
                "finalized_employees": req.get("finalized_employees", []) or [],
                "all_employee_mentions": req.get("all_employee_mentions", []) or [],
                "status": req.get("status", ""),
            }
            req["raw_requirements"] = json.dumps(raw_obj, ensure_ascii=False)

        parsed["Requirements"] = normalized_requirements
        parsed["valid_thread"] = any(bool(v.get("valid_email")) for v in normalized_requirements)
        parsed.pop("valid_email", None)

        if not parsed.get("finalized_employees"):
            union_names: List[str] = []
            seen_union = set()
            for r in normalized_requirements:
                for nm in r.get("finalized_employees", []) or []:
                    key = nm.lower()
                    if key not in seen_union:
                        seen_union.add(key)
                        union_names.append(nm)
            parsed["finalized_employees"] = union_names
        if not parsed.get("all_employee_mentions"):
            union_mentions: List[Dict[str, Any]] = []
            for r in normalized_requirements:
                union_mentions.extend(r.get("all_employee_mentions", []) or [])
            parsed["all_employee_mentions"] = union_mentions

        if (not isinstance(parsed.get("Requirements"), list)) or (len(parsed.get("Requirements") or []) == 0):
            base_req: Dict[str, Any] = {
                "shift_date": parsed.get("shift_date", ""),
                "shift_time": parsed.get("shift_time", ""),
                "shift_hours": parsed.get("shift_hours", 8),
                "location_name": parsed.get("location_name", ""),
                "client_id": parsed.get("client_id", ""),
                "finalized_employees": parsed.get("finalized_employees", []) or [],
                "all_employee_mentions": parsed.get("all_employee_mentions", []) or [],
                "valid_email": bool(parsed.get("valid_email")) or bool(parsed.get("valid_thread")),
                "status": parsed.get("status", ""),
                "is_staffing": _infer_is_staffing_from_thread(thread_text),
            }
            if base_req.get("valid_email") and not _has_sufficient_shift_details(base_req):
                base_req["valid_email"] = False
            if base_req.get("valid_email") and not base_req.get("is_staffing"):
                base_req["valid_email"] = False
            if not base_req.get("valid_email") and not _has_any_shift_detail(base_req):
                base_req["finalized_employees"] = []
            if not base_req.get("is_staffing"):
                base_req["shift_date"] = ""
                base_req["shift_time"] = ""
                base_req["location_name"] = ""
                base_req["finalized_employees"] = []
            base_req["req_key"] = _compute_req_key(base_req)
            base_req["raw_requirements"] = json.dumps(
                {
                    "req_key": base_req.get("req_key", ""),
                    "shift_date": base_req.get("shift_date", ""),
                    "shift_time": base_req.get("shift_time", ""),
                    "shift_hours": base_req.get("shift_hours", 8),
                    "location_name": base_req.get("location_name", ""),
                    "finalized_employees": base_req.get("finalized_employees", []) or [],
                    "all_employee_mentions": base_req.get("all_employee_mentions", []) or [],
                    "status": base_req.get("status", ""),
                },
                ensure_ascii=False,
            )
            base_req.pop("is_staffing", None)
            parsed["Requirements"] = [base_req]
            parsed["valid_thread"] = any(bool(v.get("valid_email")) for v in parsed.get("Requirements") or [])
            parsed.pop("valid_email", None)

        for k in [
            "shift_date",
            "shift_time",
            "shift_hours",
            "location_name",
            "client_id",
            "finalized_employees",
            "all_employee_mentions",
        ]:
            parsed.pop(k, None)

    valid_thread = bool(parsed.get("valid_thread")) if isinstance(parsed, dict) else False

    requirements: List[Dict[str, Any]] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("Requirements"), list):
        for item in parsed.get("Requirements") or []:
            if not isinstance(item, dict):
                continue
            cleaned = dict(item)
            cleaned.pop("valid_email", None)
            cleaned.pop("client_id", None)
            cleaned.pop("is_staffing", None)

            ordered: Dict[str, Any] = {}
            preferred_order = [
                "req_key",
                "shift_date",
                "shift_time",
                "shift_hours",
                "location_name",
                "finalized_employees",
                "all_employee_mentions",
                "status",
                "raw_requirements",
            ]
            for key in preferred_order:
                if key in cleaned:
                    ordered[key] = cleaned.pop(key)
            for key in list(cleaned.keys()):
                ordered[key] = cleaned[key]

            requirements.append(ordered)

    return {
        "raw_output": raw_output,
        "valid_thread": valid_thread,
        "parsed_output": {
            "requirements": requirements,
        },
    }