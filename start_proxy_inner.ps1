# cd to the script directory
cd C:\claude-message-proxy
.\.venv\Scripts\activate

if (-not (Test-Path .env)) {
    Write-Host "警告: 找不到 .env 檔案，請複製 .env.example 並命名為 .env 且填入你的 API Key！" -ForegroundColor Yellow
}
$env:INNER_MEDUSA_API_KEY = (Get-Content .env -ErrorAction SilentlyContinue | Select-String "^INNER_MEDUSA_API_KEY=" | ForEach-Object { $_.Line.Split('=', 2)[1].Trim().Trim("'").Trim('"') })
$env:INNER_MEDUSA_CHAT_URL="https://inner-medusa.genai.nchc.org.tw/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
