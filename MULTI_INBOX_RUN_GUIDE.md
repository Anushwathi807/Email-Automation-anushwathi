# JobCart Email Automation — Final Documentation

> **Primary Inbox**: `elsysayla@gmail.com`
> **Purpose**: Automatically extract structured staffing/shift data from incoming job-allocation emails
> **Server**: `http://127.0.0.1:2002`

---

## Quick Start

```bash
cd ~/Downloads/jobcart-email-automation-main
bash run_server.sh
```

That's it. The server starts and **automatically monitors `elsysayla@gmail.com`** every 5 minutes for new job emails.

---

## How It Works

```
     Incoming job emails
            │
            ▼
  ┌─────────────────────┐
  │  elsysayla@gmail.com │  ← Gmail inbox (readonly access)
  └──────────┬──────────┘
             │  Polled every 5 minutes by the watcher
             ▼
  ┌─────────────────────┐
  │   Body Cleaner      │  ← Llama 3.1 8B (fast, strips signatures/noise)
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │   Extraction Agent  │  ← Llama 3.3 70B (high-accuracy JSON extraction)
  │                     │     Pydantic schema enforcement
  │                     │     Anti-hallucination rules
  └──────────┬──────────┘
             ▼
  ┌─────────────────────┐
  │   Structured JSON   │  ← shift_date, shift_time, location,
  │   Output            │     finalized_employees, status
  └──────────┬──────────┘
             ▼
  watcher_results/extracted_results.json
```

### Dual-Model Architecture (Speed + Accuracy)

| Layer | Model | Purpose | Speed |
|:---|:---|:---|:---:|
| **Cleaning** | `llama-3.1-8b-instant` | Strip email signatures, headers, quoted replies | ⚡ Fast |
| **Extraction** | `llama-3.3-70b-versatile` | Parse shift details, employee names, IDs | 🎯 Accurate |

### How Multiple Emails Are Handled

- The watcher fetches **all threads for today** in each poll cycle
- Threads are processed in **batches of 3** with a 5-second delay between batches (rate-limit safe)
- **Deduplication**: Every processed thread ID is saved to `watcher_results/processed_thread_ids.json` — threads are never re-processed
- If a new email arrives, the next poll cycle picks it up automatically
- Rate-limit errors (429) are handled with **exponential backoff retries** (up to 3 attempts per thread)

---

## API Endpoints

| Method | Endpoint | What It Does |
|:---|:---|:---|
| `GET` | `/` | Server health check |
| `GET` | `/api/accounts_health` | Check if `elsysayla@gmail.com` is healthy |
| `GET` | `/api/watcher_status` | View all extracted results + counts |
| `GET` | `/api/watcher_trigger` | Force an immediate poll (don't wait 5 min) |
| `POST` | `/api/extract_emails` | Extract emails for a specific date |

### Example: Force Immediate Poll
```bash
curl -s http://127.0.0.1:2002/api/watcher_trigger | python3 -m json.tool
```

### Example: Extract for a Specific Date
```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"date": "2026-03-01", "accounts": ["elsysayla@gmail.com"]}' \
  http://127.0.0.1:2002/api/extract_emails | python3 -m json.tool
```

### Example: Check Watcher Results
```bash
curl -s http://127.0.0.1:2002/api/watcher_status | python3 -m json.tool
```

---

## Output Format

Each extracted thread produces:

```json
{
  "account_email": "elsysayla@gmail.com",
  "thread_id": "19ca97c4f6ed4c93",
  "valid_thread": true,
  "messages": [
    {
      "from": "client@company.com",
      "to": "elsysayla@gmail.com",
      "subject": "Staffing Request: ICU Night Shift",
      "body": "We need 2 nurses for ICU night shift..."
    }
  ],
  "extracted": {
    "shift_date": "2026-03-01",
    "shift_time": "Night",
    "shift_hours": 12,
    "location_name": "City General Hospital",
    "client_id": "SQ",
    "finalized_employees": ["T244264 Jane Doe 4377558679", "Dev Ray"],
    "status": "",
    "Requirements": [...]
  }
}
```

| Field | Meaning |
|:---|:---|
| `valid_thread` | `true` if the email is a staffing request |
| `shift_date` | ISO date (YYYY-MM-DD) |
| `shift_time` | Day / Night / Afternoon / exact times |
| `finalized_employees` | Final confirmed workers (with IDs and phone numbers if present) |
| `status` | Empty or `"delete"` if the shift was cancelled |
| `Requirements` | Array of distinct shift requirements within the thread |

---

## Configuration

| Setting | Default | How to Change |
|:---|:---|:---|
| Poll interval | 300s (5 min) | `export POLL_INTERVAL_SECONDS=120` |
| Watched email | `elsysayla@gmail.com` | `export WATCHED_EMAIL=other@gmail.com` |
| Server port | 2002 | Edit `run_server.sh` |
| Extraction model | `llama-3.3-70b-versatile` | Edit `agent/llm.py` |
| Cleaning model | `llama-3.1-8b-instant` | Edit `agent/llm.py` |
| Batch size | 3 threads | Edit `agent/inbox_watcher.py` |
| Results directory | `watcher_results/` | `export WATCHER_RESULTS_DIR=output/` |

---

## File Map

```
jobcart-email-automation-main/
├── main.py                      # Server entry point + watcher lifecycle
├── run_server.sh                # Start script
├── credentials.json             # Shared Google OAuth client
├── token.json                   # elsysayla OAuth token
├── .env                         # GROQ_API_KEY
├── requirements.txt             # Python dependencies
│
├── api/
│   └── routes.py                # All API endpoints + multi-inbox router
│
├── agent/
│   ├── llm.py                   # Model configuration (dual-model)
│   ├── tools.py                 # Gmail API integration
│   ├── token_store.py           # Token storage and health checks
│   ├── body_cleaner.py          # Email body cleaning (8B model)
│   ├── agent_runner.py          # LLM extraction pipeline (70B model)
│   └── inbox_watcher.py         # Continuous inbox poller
│
├── tokens/
│   ├── index.json               # Email → token file mapping
│   └── token_XXXXX.json         # elsysayla token
│
└── watcher_results/
    ├── extracted_results.json   # All extraction outputs
    └── processed_thread_ids.json # Dedup tracker
```

---

## Troubleshooting

| Problem | Fix |
|:---|:---|
| Server won't start | Run `pip install -r requirements.txt` |
| `Missing GROQ_API_KEY` | Add `GROQ_API_KEY=gsk_...` to `.env` |
| Account shows `bad` | Re-register: see "Re-register Account" below |
| `429 Too Many Requests` | Daily Groq limit hit; system auto-retries. Wait or upgrade at console.groq.com |
| `413 Payload Too Large` | Thread too large for model; skipped automatically |
| No threads found | Check the `date` parameter matches when emails were sent |

### Re-register Account (if token expires)
```bash
# Regenerate token
python3 connect_account.py --email elsysayla@gmail.com --label elsysayla

# Register with API
python3 -c "
import json, requests
token_json = json.load(open('token.json'))
print(requests.post('http://127.0.0.1:2002/api/accounts', json={
    'email': 'elsysayla@gmail.com',
    'token_json': token_json,
    'label': 'elsysayla'
}).json())
"
```
