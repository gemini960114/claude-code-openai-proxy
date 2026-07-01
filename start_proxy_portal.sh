#!/bin/bash
cd ~/claude-message-proxy
source .venv/bin/activate

if [ ! -f .env ]; then
    echo "警告: 找不到 .env 檔案，請複製 .env.example 並命名為 .env 且填入你的 API Key！"
fi
export INNER_MEDUSA_API_KEY=$(grep -E "^PORTAL_API_KEY=" .env 2>/dev/null | cut -d'=' -f2- | tr -d '"'\')
export INNER_MEDUSA_CHAT_URL="https://portal.genai.nchc.org.tw/api/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
