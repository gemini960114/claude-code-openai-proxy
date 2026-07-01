# cd to the script directory
cd C:\claude-message-proxy
.\.venv\Scripts\activate

if (-not (Test-Path .env)) {
    Write-Host "Warning: .env file not found. Please copy .env.example to .env and fill in your API Key." -ForegroundColor Yellow
}
$sq = [char]39
$dq = [char]34
$env:INNER_MEDUSA_API_KEY = ""
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        if ($line -like "PORTAL_API_KEY=*") {
            $val = $line.Split('=', 2)[1].Trim()
            $env:INNER_MEDUSA_API_KEY = $val.Replace($sq, "").Replace($dq, "")
        }
    }
}
$env:INNER_MEDUSA_CHAT_URL="https://portal.genai.nchc.org.tw/api/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
