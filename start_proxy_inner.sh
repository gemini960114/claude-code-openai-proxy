#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Warning: Local virtual environment (.venv) not found. Bootstrapping with uv..."
    uv venv
    source .venv/bin/activate
    uv pip install fastapi uvicorn httpx
else
    source .venv/bin/activate
fi


if [ ! -f .env ]; then
    echo "Warning: .env file not found. Please copy .env.example to .env and fill in your API Key."
fi
export BACKEND_API_KEY=$(grep -E "^INNER_MEDUSA_API_KEY=" .env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
export BACKEND_CHAT_URL="https://inner-medusa.genai.nchc.org.tw/v1/chat/completions"
export MODEL_CONFIG_PATH="$(pwd)/models_inner.json"

uvicorn proxy:app --host 127.0.0.1 --port 5000
