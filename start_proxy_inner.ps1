# cd to the script directory
cd $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host "Warning: Local virtual environment (.venv) not found. Bootstrapping with uv..." -ForegroundColor Cyan
    uv venv
    .\.venv\Scripts\activate
    uv pip install fastapi uvicorn httpx
} else {
    .\.venv\Scripts\activate
}

if (-not (Test-Path .env)) {
    Write-Host "Warning: .env file not found. Please copy .env.example to .env and fill in your API Key." -ForegroundColor Yellow
}
$sq = ([char]39).ToString()
$dq = ([char]34).ToString()
$env:INNER_MEDUSA_API_KEY = ""
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        if ($line -like "INNER_MEDUSA_API_KEY=*") {
            $val = $line.Split('=', 2)[1].Trim()
            $env:INNER_MEDUSA_API_KEY = $val.Replace($sq, "").Replace($dq, "")
        }
    }
}
$env:INNER_MEDUSA_CHAT_URL="https://inner-medusa.genai.nchc.org.tw/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
