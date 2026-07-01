#!/bin/bash
cd ~/claude-message-proxy
source .venv/bin/activate

export INNER_MEDUSA_API_KEY="sk-你的-portal-key"
export INNER_MEDUSA_CHAT_URL="https://portal.genai.nchc.org.tw/api/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
