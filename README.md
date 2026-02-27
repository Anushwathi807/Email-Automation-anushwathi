# JobCart Email Automation

An intelligent email extraction API that fetches Gmail threads for a specific date and uses AI (Groq Llama) to extract structured shift scheduling information from staffing emails.

## 📋 Features

- **Gmail Integration**: Fetches email threads from Gmail API for a specific date
- **AI-Powered Extraction**: Uses Groq's Llama model via LangChain to extract structured data
- **RESTful API**: FastAPI-based API for easy integration
- **Smart Filtering**: Automatically filters out internal communications
- **Structured Output**: Returns JSON-formatted shift scheduling information

## 🎯 What It Extracts

The system extracts the following information from emails:
- Shift date
- Shift time (Day/Afternoon/Night)
- Shift hours (defaults to 8)
- Location name
- Client ID (Q2/TFT/SQ/VAS)
- Email validity flag

## 📦 Prerequisites

Before setting up the project, ensure you have:

- **Python 3.8+** installed
- **Google Cloud Project** with Gmail API enabled
- **Groq API Key** for LLM access
- **Gmail OAuth 2.0 Credentials** (`credentials.json`)

## 🚀 Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/neurostack-git/jobcart-email-automation.git
cd jobcart-email-automation
```

### 2. Create Virtual Environment

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**On macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Google Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API**
4. Create OAuth 2.0 credentials (Desktop application)
5. Download the credentials and save as `credentials.json` in the project root

### 5. Set Up Environment Variables

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key_here
```

**Get your Groq API Key:**
1. Visit [Groq Cloud](https://console.groq.com/)
2. Sign up or log in
3. Generate an API key from the dashboard

### 6. First-Time Authentication

On first run, the application will:
1. Open a browser window for Google OAuth authentication
2. Ask you to grant Gmail read-only access
3. Generate a `token.json` file for future use

## 🏃 Running the Application

### Start the API Server

**Using Python:**
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 2002
```

**Using the provided script (Windows):**
```bash
run_server.cmd
```

The API will be available at: `http://localhost:2002`

### Access API Documentation

Once the server is running, visit:
- **Swagger UI**: http://localhost:2002/docs
- **ReDoc**: http://localhost:2002/redoc

## 📡 API Schema

### Base URL
```
http://localhost:2002
```

### Endpoints

#### 1. Health Check
```http
GET /
```

**Response:**
```json
{
  "message": "✅ Email Extraction Service is running!"
}
```

#### 2. Extract Emails
```http
POST /api/extract_emails
```

**Request Body:**
```json
{
  "date": "15/10/2025"
}
```

**Request Schema:**
| Field | Type   | Required | Format      | Description                    |
|-------|--------|----------|-------------|--------------------------------|
| date  | string | Yes      | dd/mm/yyyy  | Date to fetch emails for       |

**Response Schema (Success - 200):**
```json
{
  "date": "15/10/2025",
  "results": [
    {
      "threadId": "18f2a3b4c5d6e7f8",
      "extracted": {
        "shift_date": "2025-10-15",
        "shift_time": "Day",
        "shift_hours": 8,
        "location_name": "Toronto General Hospital",
        "client_id": "Q2",
        "valid_email": true
      }
    }
  ]
}
```

**Response Schema (No Threads):**
```json
{
  "date": "15/10/2025",
  "results": [],
  "message": "No threads found"
}
```

**Response Schema (Error - 500):**
```json
{
  "detail": "Error message here"
}
```

### Response Fields

| Field                    | Type    | Description                                        |
|--------------------------|---------|---------------------------------------------------|
| date                     | string  | The date that was queried                         |
| results                  | array   | Array of extracted email thread data              |
| results[].threadId       | string  | Gmail thread ID                                   |
| results[].extracted      | object  | Extracted structured data                         |
| shift_date               | string  | Date of the shift (ISO format)                    |
| shift_time               | string  | Time of shift (Day/Afternoon/Night)               |
| shift_hours              | number  | Number of hours for the shift                     |
| location_name            | string  | Name of the worksite/location                     |
| client_id                | string  | Client identifier (Q2/TFT/SQ/VAS)                 |
| valid_email              | boolean | Whether the email contains relevant information   |
| raw_output               | string  | Raw LLM output (if JSON parsing fails)            |

## 🔧 API Usage Examples & Responses

### Example 1: Basic Request with cURL

**Request:**
```bash
curl -X POST "http://localhost:2002/api/extract_emails" \
  -H "Content-Type: application/json" \
  -d '{"date": "15/10/2025"}'
```

**Response (Success):**
```json
{
  "date": "15/10/2025",
  "results": [
    {
      "threadId": "18f2a3b4c5d6e7f8",
      "extracted": {
        "shift_date": "2025-10-15",
        "shift_time": "Day",
        "shift_hours": 8,
        "location_name": "Toronto General Hospital",
        "client_id": "Q2",
        "valid_email": true
      }
    },
    {
      "threadId": "29g3b4c5d6e7f8g9",
      "extracted": {
        "shift_date": "2025-10-15",
        "shift_time": "Night",
        "shift_hours": 12,
        "location_name": "St. Michael's Hospital",
        "client_id": "TFT",
        "valid_email": true
      }
    }
  ]
}
```

---

### Example 2: Request with No Results

**Request:**
```bash
curl -X POST "http://localhost:2002/api/extract_emails" \
  -H "Content-Type: application/json" \
  -d '{"date": "01/01/2025"}'
```

**Response (No Threads Found):**
```json
{
  "date": "01/01/2025",
  "results": [],
  "message": "No threads found"
}
```

---

### Example 3: Using Python with Full Response Handling

**Code:**
```python
import requests
import json

# API endpoint
url = "http://localhost:2002/api/extract_emails"

# Request payload
payload = {"date": "15/10/2025"}

# Make POST request
response = requests.post(url, json=payload)

# Parse response
if response.status_code == 200:
    data = response.json()
    print(f"✅ Found {len(data['results'])} email threads for {data['date']}")
    
    # Display each result
    for i, result in enumerate(data['results'], 1):
        ext = result['extracted']
        print(f"\n📧 Email Thread {i}:")
        print(f"   Thread ID: {result['threadId']}")
        print(f"   Location: {ext['location_name']}")
        print(f"   Shift Date: {ext['shift_date']}")
        print(f"   Shift Time: {ext['shift_time']}")
        print(f"   Hours: {ext['shift_hours']}")
        print(f"   Client: {ext['client_id']}")
        print(f"   Valid: {ext['valid_email']}")
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)
```

**Console Output:**
```
✅ Found 2 email threads for 15/10/2025

📧 Email Thread 1:
   Thread ID: 18f2a3b4c5d6e7f8
   Location: Toronto General Hospital
   Shift Date: 2025-10-15
   Shift Time: Day
   Hours: 8
   Client: Q2
   Valid: True

📧 Email Thread 2:
   Thread ID: 29g3b4c5d6e7f8g9
   Location: St. Michael's Hospital
   Shift Date: 2025-10-15
   Shift Time: Night
   Hours: 12
   Client: TFT
   Valid: True
```

---

### Example 4: Using JavaScript (Node.js)

**Code:**
```javascript
const url = "http://localhost:2002/api/extract_emails";
const payload = { date: "15/10/2025" };

async function extractEmails() {
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    
    if (response.ok) {
      console.log(`✅ Found ${data.results.length} email threads\n`);
      
      data.results.forEach((result, index) => {
        const ext = result.extracted;
        console.log(`📧 Email Thread ${index + 1}:`);
        console.log(`   Location: ${ext.location_name}`);
        console.log(`   Date: ${ext.shift_date}`);
        console.log(`   Time: ${ext.shift_time}`);
        console.log(`   Hours: ${ext.shift_hours}`);
        console.log(`   Client: ${ext.client_id}\n`);
      });
    } else {
      console.error(`❌ Error: ${response.status}`);
    }
  } catch (error) {
    console.error("❌ Request failed:", error);
  }
}

extractEmails();
```

**Console Output:**
```
✅ Found 2 email threads

📧 Email Thread 1:
   Location: Toronto General Hospital
   Date: 2025-10-15
   Time: Day
   Hours: 8
   Client: Q2

📧 Email Thread 2:
   Location: St. Michael's Hospital
   Date: 2025-10-15
   Time: Night
   Hours: 12
   Client: TFT
```

---

### Example 5: Processing Multiple Dates (Python)

**Code:**
```python
import requests
from datetime import datetime, timedelta

url = "http://localhost:2002/api/extract_emails"

# Get emails for last 7 days
start_date = datetime.now()
all_shifts = []

print("📅 Fetching emails for the last 7 days...\n")

for i in range(7):
    date = start_date - timedelta(days=i)
    date_str = date.strftime("%d/%m/%Y")
    
    response = requests.post(url, json={"date": date_str})
    data = response.json()
    
    print(f"Date: {date_str} - Found {len(data['results'])} threads")
    
    for result in data['results']:
        ext = result['extracted']
        if ext.get('valid_email'):
            all_shifts.append(ext)

print(f"\n✅ Total shifts found: {len(all_shifts)}")
```

**Console Output:**
```
📅 Fetching emails for the last 7 days...

Date: 15/10/2025 - Found 2 threads
Date: 14/10/2025 - Found 1 threads
Date: 13/10/2025 - Found 0 threads
Date: 12/10/2025 - Found 3 threads
Date: 11/10/2025 - Found 1 threads
Date: 10/10/2025 - Found 0 threads
Date: 09/10/2025 - Found 2 threads

✅ Total shifts found: 9
```

---

### Example 6: Error Response

**Request with Server Error:**
```bash
curl -X POST "http://localhost:2002/api/extract_emails" \
  -H "Content-Type: application/json" \
  -d '{"date": "invalid"}'
```

**Response (Error - 500):**
```json
{
  "detail": "time data 'invalid' does not match format '%d/%m/%Y'"
}
```

---

### Example 7: Integration with React Frontend

**Code:**
```javascript
import React, { useState } from 'react';

function EmailExtractor() {
  const [date, setDate] = useState('15/10/2025');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const handleExtract = async () => {
    setLoading(true);
    try {
      const response = await fetch('http://localhost:2002/api/extract_emails', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date }),
      });
      
      const data = await response.json();
      setResults(data.results);
    } catch (error) {
      console.error('Error:', error);
    }
    setLoading(false);
  };

  return (
    <div>
      <input 
        value={date} 
        onChange={(e) => setDate(e.target.value)}
        placeholder="dd/mm/yyyy"
      />
      <button onClick={handleExtract} disabled={loading}>
        {loading ? 'Loading...' : 'Extract Emails'}
      </button>
      
      <div>
        {results.map((result, i) => (
          <div key={i}>
            <h3>{result.extracted.location_name}</h3>
            <p>Date: {result.extracted.shift_date}</p>
            <p>Time: {result.extracted.shift_time}</p>
            <p>Hours: {result.extracted.shift_hours}</p>
            <p>Client: {result.extracted.client_id}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Rendered Output:**
```
Toronto General Hospital
Date: 2025-10-15
Time: Day
Hours: 8
Client: Q2

St. Michael's Hospital
Date: 2025-10-15
Time: Night
Hours: 12
Client: TFT
```

## 📂 Project Structure

```
jobcart-email-automation/
│
├── agent/                      # AI Agent modules
│   ├── agent_runner.py         # Main extraction logic
│   ├── llm.py                  # Groq LLM initialization
│   └── tools.py                # Gmail API integration
│
├── api/                        # API layer
│   └── routes.py               # FastAPI route definitions
│
├── credentials.json            # Google OAuth credentials (not in repo)
├── token.json                  # Generated OAuth token (not in repo)
├── .env                        # Environment variables (not in repo)
│
├── main.py                     # FastAPI application entry point
├── requirements.txt            # Python dependencies
├── run_server.cmd              # Windows server startup script
│
├── gmail_test.py               # Test script for Gmail API
├── gmail_threads_lastNDays.py  # Utility to fetch last N days
├── gmail_threads_on_date.py    # Utility to fetch specific date
│
├── .gitignore                  # Git ignore rules
└── README.md                   # This file
```

## 🔐 Security Notes

- Never commit `credentials.json`, `token.json`, or `.env` files to version control
- Keep your Groq API key secure
- The application uses Gmail readonly scope only
- Internal QStaff emails (@qstaff.ca) are automatically filtered out

## 🛠️ Dependencies

- **fastapi**: Web framework for building APIs
- **uvicorn**: ASGI server for running FastAPI
- **google-api-python-client**: Gmail API client
- **google-auth**: Google authentication
- **langchain**: LLM framework
- **langchain-groq**: Groq integration for LangChain
- **python-dotenv**: Environment variable management

## 📝 Notes

- The system processes emails with an after/before date query
- Emails from internal staff (@qstaff.ca) are automatically skipped
- The LLM uses temperature=0.3 for consistent extractions
- Maximum token limit is set to 8192 for comprehensive email parsing
- Streaming is enabled for better performance

## 🐛 Troubleshooting

### Issue: "Missing GROQ_API_KEY in .env file"
**Solution**: Create a `.env` file and add your Groq API key

### Issue: "credentials.json not found"
**Solution**: Download OAuth credentials from Google Cloud Console and place in project root

### Issue: Authentication browser window doesn't open
**Solution**: Make sure you're running the server in an environment with browser access, or manually authorize using the provided URL

### Issue: No threads found
**Solution**: Verify the date format is "dd/mm/yyyy" and that emails exist for that date

## � License

This project is part of JobCart automation system.

## 👥 Support

For issues or questions, please open an issue on the GitHub repository.
