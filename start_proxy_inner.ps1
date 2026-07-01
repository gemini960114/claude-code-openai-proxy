# cd to the script directory
cd C:\claude-message-proxy
.\.venv\Scripts\activate

$env:INNER_MEDUSA_API_KEY="sk-你的-inner-medusa-key"
$env:INNER_MEDUSA_CHAT_URL="https://inner-medusa.genai.nchc.org.tw/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
