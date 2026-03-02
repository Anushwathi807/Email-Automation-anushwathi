# agent/tools.py
from __future__ import annotations
import os, base64, json, re, datetime, logging
from typing import Any, Dict, List, Tuple, Set
from email.utils import parseaddr, getaddresses

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail read-only scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
logger = logging.getLogger("agent.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ────────────────────────────────
#  Authentication
# ────────────────────────────────
def get_creds():
    creds = None
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = bool(creds.valid)
            except Exception:
                refreshed = False

        if not refreshed:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        with open('token.json', 'w') as f:
            f.write(creds.to_json())
    return creds


# ────────────────────────────────
#  Helper functions
# ────────────────────────────────
def b64url_decode(data: str) -> bytes:
    if not data:
        return b""
    padding = '=' * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)

def headers_to_dict(headers: List[Dict[str, str]]) -> Dict[str, str]:
    return {h['name']: h['value'] for h in headers}

def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html or '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_body(payload: Dict[str, Any]) -> str:
    mime = payload.get('mimeType', '')
    body = payload.get('body', {}) or {}
    data = body.get('data')

    if mime.startswith('text/plain') and data:
        return b64url_decode(data).decode(errors='ignore')

    if mime.startswith('text/html') and data:
        html = b64url_decode(data).decode(errors='ignore')
        return _strip_html(html)

    for part in (payload.get('parts') or []):
        text = get_body(part)
        if text:
            return text

    if data:
        try:
            return b64url_decode(data).decode(errors='ignore')
        except Exception:
            return ""
    return ""

def strip_quotes(body: str) -> str:
    if not body:
        return ""
    body = "\n".join([ln for ln in body.splitlines() if not ln.strip().startswith(">")])
    for pat in [
        r"\nOn .+ wrote:\n",
        r"\n-----Original Message-----\n",
        r"\nFrom: .*Sent: .*To: .*Subject: .*\n",
    ]:
        m = re.search(pat, body, flags=re.IGNORECASE)
        if m:
            return body[:m.start()].strip()
    return body.strip()

def _is_internal_sender(from_header: str) -> bool:
    _, email = parseaddr(from_header or "")
    email = (email or "").strip().lower()
    return email.endswith("@qstaff.ca")

def _extract_emails(header_value: str) -> List[str]:
    if not header_value:
        return []
    return [email for _, email in getaddresses([header_value]) if email]

def _extract_domain(addr: str) -> str:
    if not addr:
        return ""
    _, email = parseaddr(addr)
    email = email.strip().lower()
    if '@' in email:
        return email.split('@', 1)[1].strip()
    return ""

def _primary_recipient_domain(to_header: str) -> str:
    emails = _extract_emails(to_header)
    if not emails:
        return ""
    return _extract_domain(emails[0])

def _same_domain_sender_vs_primary_recipient(sender: str, to_header: str) -> bool:
    sdom = _extract_domain(sender)
    rdom = _primary_recipient_domain(to_header)
    internal_domains = {"qstaff.ca"}
    return bool(sdom and rdom and sdom == rdom and sdom in internal_domains)

# ────────────────────────────────
#  Gmail Thread Fetch
# ────────────────────────────────
def _parse_process_date(date_str: str) -> datetime.date:
    s = (date_str or "").strip()
    if not s:
        raise ValueError("date is required")
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        raise ValueError('Invalid date format. Use "YYYY-MM-DD".')

def list_threads_on_date(service, target_date: str, custom_query: str = None) -> List[str]:
    d = _parse_process_date(target_date)
    next_day = d + datetime.timedelta(days=1)
    
    query = f"after:{d.isoformat()} before:{next_day.isoformat()}"
    if custom_query:
        query = f"({custom_query}) {query}"
        
    logger.info(f"📡 Requesting Gmail threads with query: {query}")
    res = service.users().threads().list(userId='me', q=query, maxResults=100).execute()
    return [t['id'] for t in res.get('threads', [])]

def fetch_thread(service, thread_id: str) -> Dict[str, Any]:
    thread = service.users().threads().get(userId='me', id=thread_id, format='full').execute()
    messages = []
    for msg in thread.get('messages', []):
        payload = msg.get('payload', {}) or {}
        hdrs = headers_to_dict(payload.get('headers', []))
        from_h = hdrs.get("From", "") or ""
        to_h = hdrs.get("To", "") or ""
        cc_h = hdrs.get("Cc", "") or ""
        subject_h = hdrs.get("Subject", "") or ""
        date_h = hdrs.get("Date", "") or ""

        if _same_domain_sender_vs_primary_recipient(from_h, to_h):
            continue

        body_text = strip_quotes(get_body(payload))
        parsed_body = body_text if not _is_internal_sender(from_h) else ""
        messages.append({
            "id": msg.get("id"),
            "from": from_h,
            "to": to_h,
            "cc": cc_h,
            "subject": subject_h,
            "date": date_h,
            "body": body_text,
            "parsed_body": parsed_body,
        })
    return {"threadId": thread_id, "messages": messages}

# ────────────────────────────────
#  Public API Function for Backend
# ────────────────────────────────
def build_gmail_service_from_refresh_token(refresh_token: str, token_json: Dict[str, Any] | None = None):
    token_json = token_json or {}

    client_id = token_json.get("client_id")
    client_secret = token_json.get("client_secret")
    token_uri = token_json.get("token_uri") or os.getenv(
        "GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token"
    )

    if not (client_id and client_secret) and os.path.exists("credentials.json"):
        try:
            with open("credentials.json", "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            block = cfg.get("installed") or cfg.get("web") or {}
            client_id = block.get("client_id") or client_id
            client_secret = block.get("client_secret") or client_secret
            token_uri = block.get("token_uri") or token_uri
        except Exception:
            pass

    client_id = client_id or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = client_secret or os.getenv("GOOGLE_CLIENT_SECRET")

    if not (client_id and client_secret):
        raise RuntimeError("Missing OAuth client config")

    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)

def get_threads_for_date_with_service(service, date_str: str, custom_query: str = None):
    thread_ids = list_threads_on_date(service, date_str, custom_query)
    threads: List[Dict[str, Any]] = []

    for tid in thread_ids:
        thread_data = fetch_thread(service, tid)
        if thread_data['messages']:
            first_sender = (thread_data['messages'][0].get('from') or '').lower()
            if "@qstaff.ca" in first_sender:
                continue
        if not thread_data['messages']:
            continue
        threads.append(thread_data)
    return threads

def get_threads_for_date(date_str: str):
    creds = get_creds()
    gmail = build('gmail', 'v1', credentials=creds)
    return get_threads_for_date_with_service(gmail, date_str)