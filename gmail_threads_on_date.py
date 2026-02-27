from __future__ import annotations
import os, base64, json, re, datetime
from typing import Any, Dict, List
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail read-only scope
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# ────────────────────────────────
#  Authentication
# ────────────────────────────────
def get_creds():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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

def get_body(payload: Dict[str, Any]) -> str:
    mime = payload.get('mimeType', '')
    data = payload.get('body', {}).get('data')
    if mime == 'text/plain' and data:
        return b64url_decode(data).decode(errors='ignore')
    if mime == 'text/html' and data:
        html = b64url_decode(data).decode(errors='ignore')
        return re.sub('<[^<]+?>', '', html)
    for part in payload.get('parts', []) or []:
        text = get_body(part)
        if text:
            return text
    return ""

def strip_quotes(body: str) -> str:
    body = "\n".join([ln for ln in body.splitlines() if not ln.strip().startswith(">")])
    for pat in [r"\nOn .+ wrote:\n", r"\n-----Original Message-----\n", r"\nFrom: .*Sent: .*To: .*Subject: .*\n"]:
        m = re.search(pat, body, flags=re.IGNORECASE)
        if m:
            return body[:m.start()].strip()
    return body.strip()

# ────────────────────────────────
#  Gmail Thread Fetch
# ────────────────────────────────
def list_threads_on_date(service, target_date: str) -> List[str]:
    """
    target_date: string in "dd/mm/yyyy" format.
    Uses Gmail's query syntax: after:<date> before:<date+1>
    """
    day, month, year = map(int, target_date.split("/"))
    d = datetime.date(year, month, day)
    next_day = d + datetime.timedelta(days=1)
    query = f"after:{d.isoformat()} before:{next_day.isoformat()}"
    res = service.users().threads().list(userId='me', q=query, maxResults=100).execute()
    return [t['id'] for t in res.get('threads', [])]

def fetch_thread(service, thread_id: str) -> Dict[str, Any]:
    thread = service.users().threads().get(userId='me', id=thread_id, format='full').execute()
    messages = []
    for msg in thread.get('messages', []):
        hdrs = headers_to_dict(msg['payload'].get('headers', []))
        body_text = strip_quotes(get_body(msg['payload']))
        messages.append({
            "id": msg.get("id"),
            "from": hdrs.get("From"),
            "to": hdrs.get("To"),
            "cc": hdrs.get("Cc"),
            "subject": hdrs.get("Subject"),
            "date": hdrs.get("Date"),
            "body": body_text
        })
    return {"threadId": thread_id, "messages": messages}

# ────────────────────────────────
#  Main
# ────────────────────────────────
if __name__ == "__main__":
    creds = get_creds()
    gmail = build('gmail', 'v1', credentials=creds)

    # 🗓️ Set the date you want to fetch (format: "dd/mm/yyyy")
    date = "06/10/2025"  # change this as needed

    print(f"Fetching Gmail threads for {date}...\n")
    thread_ids = list_threads_on_date(gmail, date)

    if not thread_ids:
        print(f"No conversations found on {date}.")
    else:
        print(f"Found {len(thread_ids)} threads.\n")
        threads_data = [fetch_thread(gmail, tid) for tid in thread_ids]
        print(json.dumps(threads_data, indent=2, ensure_ascii=False))
