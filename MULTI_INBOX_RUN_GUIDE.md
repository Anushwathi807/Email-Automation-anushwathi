# 📬 Multi-Inbox Email Automation — Run Guide

> **3 Accounts**: `elsysayla@gmail.com` · `anushwathiranganathan@gmail.com` · `anushwathircps10@gmail.com`

---

## Prerequisites

| Requirement | Where |
|:---|:---|
| Python 3.10+ | System |
| `credentials.json` | Project root (shared OAuth client) |
| `.env` with `GROQ_API_KEY` | Project root |
| Tokens for all 3 accounts | `tokens/` directory (registered via API) |

```bash
# Install dependencies (one-time)
pip install -r requirements.txt
```

---

## 1. Start the Server

```bash
cd ~/Downloads/jobcart-email-automation-main
bash run_server.sh
```

Server starts at **http://127.0.0.1:2002**. Keep this terminal open.

---

## 2. Verify All Accounts Are Healthy

Open a **new terminal** and run:

```bash
curl -s http://127.0.0.1:2002/api/accounts_health | python3 -m json.tool
```

**Expected output** — all 3 accounts show `"status": "healthy"`:

```json
{
  "accounts": [
    { "account_email": "anushwathiranganathan@gmail.com", "status": "healthy" },
    { "account_email": "anushwathircps10@gmail.com",      "status": "healthy" },
    { "account_email": "elsysayla@gmail.com",              "status": "healthy" }
  ]
}
```

> **If an account is missing**, re-register it (see Section 5 below).

---

## 3. Test Email-to-Email Communication

### Step A: Send Test Emails (Manual — use browser or phone)

Send these 3 test emails to create a cross-communication loop:

| # | From → To | Subject |
|---|:---|:---|
| 1 | `anushwathircps10` → `anushwathiranganathan` | Staffing Request: ICU Night Shift |
| 2 | `elsysayla` → `anushwathircps10` | Shift Confirmation: ER Day Shift |
| 3 | `anushwathiranganathan` → `elsysayla` | Schedule Update: NICU Weekend |

**Sample body** (paste into any test email):
```
Hi, we need 2 nurses for the ICU night shift on March 1st, 2026.
Time: 7 PM to 7 AM (12 hours).
Location: City General Hospital.
Please confirm Jane Doe and John Smith.
```

### Step B: Extract Emails from All 3 Inboxes

Wait ~1 minute after sending, then run:

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{
    "date": "2026-03-01",
    "accounts": [
      "anushwathiranganathan@gmail.com",
      "anushwathircps10@gmail.com",
      "elsysayla@gmail.com"
    ]
  }' \
  http://127.0.0.1:2002/api/extract_emails | python3 -m json.tool
```

### Step C: What to Look For in the Output

Each result entry tells you:
- `account_email` — which inbox was scanned
- `messages[].from` / `messages[].to` — confirms cross-account communication
- `valid_thread` — `true` if the LLM identified it as a staffing email
- `extracted` — structured shift data (date, time, hours, location, employees)

```
✅ PASS if: You see threads where "from" is one account and "to" is another
✅ PASS if: valid_thread = true for staffing emails
✅ PASS if: extracted fields contain the shift details you typed
```

---

## 4. Extract from a Single Inbox

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"date": "2026-03-01", "accounts": ["elsysayla@gmail.com"]}' \
  http://127.0.0.1:2002/api/extract_emails | python3 -m json.tool
```

---

## 5. Re-register an Account (if needed)

If an account shows `"status": "bad"` or is missing, re-register it:

### For `elsysayla` (uses root `token.json`):
```bash
python3 -c "
import json, requests
token_json = json.load(open('token.json'))
requests.post('http://127.0.0.1:2002/api/accounts', json={
    'email': 'elsysayla@gmail.com',
    'token_json': token_json,
    'label': 'elsysayla'
}).json()
" 
```

### For `anushwathiranganathan` or `anushwathircps10`:
```bash
python3 -c "
import json, requests
payload = json.load(open('token_outputs/token_output_anushwathiranganathan.json'))
print(requests.post('http://127.0.0.1:2002/api/accounts', json=payload).json())
"
```

### Generate a fresh token (if expired):
```bash
python3 connect_account.py --email YOUR_EMAIL@gmail.com --label YOUR_LABEL
```

---

## 6. How It Works (The Mechanism)

```
┌─────────────────────────────────────────────────────────────┐
│                    credentials.json                         │
│              (Shared Google OAuth Client)                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌─────────┐   ┌─────────┐
   │ Account1 │   │ Account2 │   │ Account3 │
   │ elsysayla│   │ anush..R │   │ anush..10│
   │ token.json   │ token_   │   │ token_   │
   │          │   │ 61453.json   │ 76829.json
   └────┬─────┘   └────┬─────┘   └────┬─────┘
        │              │              │
        └──────────────┼──────────────┘
                       ▼
              POST /api/extract_emails
              {"date": "...", "accounts": [...]}
                       │
                       ▼
              ┌────────────────┐
              │  Router Loop   │
              │ (routes.py)    │
              │                │
              │ For each email:│
              │ 1. Load token  │
              │ 2. Build Gmail │
              │    service     │
              │ 3. Fetch       │
              │    threads     │
              │ 4. Run LLM     │
              │    extraction  │
              └────────┬───────┘
                       │
                       ▼
              Structured JSON Output
              (shift details per thread)
```

**Key points:**
- All accounts share **one OAuth client** but each has its **own refresh token**
- The API uses `gmail.readonly` scope — it can **read** from any registered inbox but cannot send
- Gmail→Gmail emails between your accounts are **not filtered** (only internal `@qstaff.ca` messages are skipped)
- Groq rate limits (429 errors) are handled automatically with exponential backoff

---

## Quick Reference

| Action | Command |
|:---|:---|
| Start server | `bash run_server.sh` |
| Health check | `curl -s http://127.0.0.1:2002/api/accounts_health \| python3 -m json.tool` |
| Extract all | `curl -s -X POST -H "Content-Type: application/json" -d '{"date":"YYYY-MM-DD","accounts":["a@gmail.com","b@gmail.com","c@gmail.com"]}' http://127.0.0.1:2002/api/extract_emails \| python3 -m json.tool` |
| Stop server | `Ctrl+C` in the server terminal |
