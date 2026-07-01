#!/bin/bash
cd ~/claude-message-proxy
source .venv/bin/activate

if [ ! -f .env ]; then
    echo "Warning: .env file not found. Please copy .env.example to .env and fill in your API Key."
fi
export INNER_MEDUSA_API_KEY=$(grep -E "^PORTAL_API_KEY=" .env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
export INNER_MEDUSA_CHAT_URL="https://portal.genai.nchc.org.tw/api/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
