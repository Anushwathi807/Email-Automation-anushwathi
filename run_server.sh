#!/bin/bash

echo ==============================================
echo "  🚀 Starting Email Extraction FastAPI Server"
echo ==============================================

# Activate virtual environment if exists
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Run Uvicorn with auto-reload and detailed logging
uvicorn main:app \
    --host 0.0.0.0 \
    --port 2002 \
    --reload \
    --reload-delay 1 \
    --log-level info \
    --access-log

echo ""
echo "✅ Server running at: http://127.0.0.1:2002"
echo "Press CTRL+C to stop."
