# Claude Code 外接 OpenAI-Compatible API：FastAPI Proxy 安裝手冊 (macOS 版本)

## 1. 目的說明

本手冊說明如何在 macOS 本機架設一個 FastAPI Proxy，讓 Claude Code 可以透過 Anthropic-compatible `/v1/messages` 介面呼叫後端 OpenAI-compatible API。

本次需求背景如下：

```text
Claude Code
→ /v1/messages
→ FastAPI Proxy
→ inner-medusa /v1/chat/completions
→ MiniMax-M2.7 / GLM-5.2 / Thanos3.5-397B-A17B
```

由於 Claude Code 會使用 `/v1/messages`，而後端 `inner-medusa` 已確認可正常使用 `/v1/chat/completions`，因此透過自製 FastAPI Proxy 將 Claude Code 的 Anthropic Messages API 格式轉換為 OpenAI Chat Completions 格式。

先前測試中也確認 LiteLLM 的 `/v1/chat/completions` 可正常轉接，但 LiteLLM 的 `/v1/messages` 會轉往後端 `/v1/responses`，導致 nginx 403，因此本方案改用 FastAPI 明確轉接到 `/v1/chat/completions`。

---

## 2. 架構圖

```text
Claude Code
  |
  |  Anthropic-compatible API
  |  POST http://127.0.0.1:5000/v1/messages
  v
FastAPI Proxy
  |
  |  OpenAI-compatible API
  |  POST https://inner-medusa.genai.nchc.org.tw/v1/chat/completions
  v
inner-medusa API Gateway
  |
  v
後端模型
  - MiniMax-M2.7
  - GLM-5.2
  - Thanos3.5-397B-A17B
```

---

## 3. 環境需求

### 作業系統

```text
macOS 12+ (Monterey, Ventura, Sonoma, Sequoia 等)
```

### 必要工具

```text
uv
Python 3.10+
Claude Code
Terminal / zsh / bash
```

確認 uv 是否可用：

```bash
uv --version
```

---

## 4. 複製專案與進入專案目錄

開啟終端機 (Terminal)，將此專案複製到你想要的位置，並進入該專案目錄：

```bash
git clone https://github.com/gemini960114/claude-code-openai-proxy.git
cd claude-code-openai-proxy
```

---

## 5. 建立 Python 虛擬環境

在專案目錄下執行以下指令建立並啟用虛擬環境（如果你直接執行啟動腳本，腳本也會自動為你初始化此環境與套件）：

```bash
uv venv
source .venv/bin/activate
```

成功後，終端機提示字元前方會出現類似：

```text
(.venv) user@macbook claude-code-openai-proxy %
```

---

## 6. 安裝必要套件

```bash
uv pip install fastapi uvicorn httpx
```

確認套件已安裝：

```bash
uv pip list
```

應可看到：

```text
fastapi
uvicorn
httpx
```

---

## 7. 建立 FastAPI Proxy 程式

在 `~/claude-message-proxy` 目錄下建立檔案：

```text
proxy.py
```

內容與 Windows 版本相同，可直接複製：

```python
import os
import time
import uuid
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI()

BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")
BACKEND_CHAT_URL = os.environ.get(
    "BACKEND_CHAT_URL",
    "https://portal.genai.nchc.org.tw/api/v1/chat/completions",
)

# 預設為 ["*"] 代表支援後端所有模型。若要限制特定模型，請在此列出模型名稱作為白名單。
SUPPORTED_MODELS = ["*"]


def extract_text_from_anthropic_content(content):
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(item.get("text", ""))
        return "\n".join(parts)

    return ""


def anthropic_messages_to_openai(payload: dict) -> dict:
    messages = []

    system = payload.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            system_text = extract_text_from_anthropic_content(system)
            if system_text:
                messages.append({"role": "system", "content": system_text})

    for msg in payload.get("messages", []):
        role = msg.get("role", "user")
        content = extract_text_from_anthropic_content(msg.get("content", ""))

        if role not in ["system", "user", "assistant"]:
            role = "user"

        messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    return {
        "model": payload.get("model", "MiniMax-M2.7"),
        "messages": messages,
        "max_tokens": payload.get("max_tokens", 1024),
        "temperature": payload.get("temperature", 0.7),
        "stream": False,
    }


def openai_to_anthropic(openai_resp: dict, model: str) -> dict:
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")

    usage = openai_resp.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "text",
                "text": content or "",
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "claude-message-proxy",
        "backend": BACKEND_CHAT_URL,
    }


@app.get("/v1/models")
async def models():
    # 嘗試從後端動態獲取所有模型清單
    models_url = BACKEND_CHAT_URL.replace("/chat/completions", "/models")
    headers = {}
    if BACKEND_API_KEY:
        headers["Authorization"] = f"Bearer {BACKEND_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(models_url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            backend_models = []
            if isinstance(data, dict) and "data" in data:
                backend_models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
            elif isinstance(data, list):
                backend_models = data

            # 如果設定為 ["*"] 則返回所有模型，否則使用白名單過濾
            if SUPPORTED_MODELS and "*" not in SUPPORTED_MODELS:
                display_models = [m for m in backend_models if m in SUPPORTED_MODELS]
            else:
                display_models = backend_models

            return {
                "object": "list",
                "data": [
                    {
                        "id": model,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "inner-medusa",
                    }
                    for model in display_models
                ],
            }
    except Exception:
        # 當無法聯絡後端時，回退到靜態白名單
        pass

    # 靜態回退處理 (排除 "*")
    fallback_models = [m for m in SUPPORTED_MODELS if m != "*"]
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "inner-medusa",
            }
            for model in fallback_models
        ],
    }


@app.post("/v1/messages")
async def messages(request: Request):
    if not BACKEND_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="BACKEND_API_KEY is not set",
        )

    payload = await request.json()
    model = payload.get("model", "MiniMax-M2.7")

    openai_payload = anthropic_messages_to_openai(payload)

    headers = {
        "Authorization": f"Bearer {BACKEND_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(
            BACKEND_CHAT_URL,
            headers=headers,
            json=openai_payload,
        )

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=resp.text,
        )

    data = resp.json()
    return JSONResponse(openai_to_anthropic(data, model))
```

---

## 8. 建立並設定本地環境變數檔案 (.env)

請在專案目錄下複製 `.env.example` 命名為 `.env`：

```bash
cp .env.example .env
```

接著編輯 `.env`，將佔位符替換為你的真實 API Key：

```text
INNER_MEDUSA_API_KEY="sk-你的-inner-medusa-key"
PORTAL_API_KEY="sk-你的-portal-key"
```

> [!IMPORTANT]
> 為了安全性，請勿將已設定金鑰的 `.env` 檔案上傳到 GitHub。`.gitignore` 檔案已預設將 `.env` 排除。


---

## 9. 啟動 FastAPI Proxy

```bash
uvicorn proxy:app --host 127.0.0.1 --port 5000
```

成功後會看到類似：

```text
INFO:     Uvicorn running on http://127.0.0.1:5000
```

此終端機視窗需要保持開啟。

---

## 10. 測試 FastAPI Proxy

開啟另一個終端機視窗，測試 health check：

```bash
curl -i "http://127.0.0.1:5000/health"
```

預期結果：

```json
{
  "status": "ok",
  "service": "claude-message-proxy",
  "backend": "https://portal.genai.nchc.org.tw/api/v1/chat/completions"
}
```

---

## 11. 測試 `/v1/messages`

```bash
curl -i "http://127.0.0.1:5000/v1/messages" \
  -H "x-api-key: anything" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  --data-raw '{"model":"gemma-4-31B-it","max_tokens":128,"messages":[{"role":"user","content":"hello, reply with one short sentence"}]}'
```

若成功，應回傳類似 Anthropic Messages API 格式：

```json
{
  "id": "msg_xxxxx",
  "type": "message",
  "role": "assistant",
  "model": "gemma-4-31B-it",
  "content": [
    {
      "type": "text",
      "text": "Hello! How can I help you today?"
    }
  ],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  }
}
```

---

## 12. 設定 Claude Code

編輯 Claude Code 的 `settings.json`。

在 macOS 上通常位置為：

```text
~/.claude/settings.json
```

範例設定如下：

```json
{
  "env": {
    "ANTHROPIC_API_KEY": "anything",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:5000",
    "NO_PROXY": "localhost,127.0.0.1",
    "no_proxy": "localhost,127.0.0.1",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "GLM-5.2",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "GLM-5.2",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "Thanos3.5-397B-A17B",
    "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "Thanos3.5-397B-A17B",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "MiniMax-M2.7",
    "ANTHROPIC_MODEL": "GLM-5.2",
    "CLAUDE_CODE_DISABLE_THINKING": "1",
    "LITELLM_DROP_PARAMS": "true",
    "ANTHROPIC_DISABLE_THINKING": "1",
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"
  },
  "attribution": {
    "commit": "",
    "pr": ""
  },
  "model": "haiku",
  "promptSuggestionEnabled": false,
  "plansDirectory": "./plans",
  "prefersReducedMotion": true,
  "theme": "dark",
  "terminalProgressBarEnabled": false
}
```

---

## 13. 啟動 Claude Code 測試

確認 FastAPI Proxy 還在執行後，重新開啟終端機，執行：

```bash
claude --debug
```

輸入：

```text
hi
```

若成功，Claude Code 會透過：

```text
http://127.0.0.1:5000/v1/messages
```

再由 FastAPI Proxy 轉送到：

```text
https://inner-medusa.genai.nchc.org.tw/v1/chat/completions
```

---

## 14. 常見問題排查

### 問題 1：Claude Code 顯示 Please run /login

請確認 `settings.json` 有正確設定：

```json
"ANTHROPIC_BASE_URL": "http://127.0.0.1:5000"
```

並確認 FastAPI Proxy 已啟動。

---

### 問題 2：FastAPI 回 500，顯示 BACKEND_API_KEY is not set

代表沒有設定 API key。

請重新設定：

```bash
export BACKEND_API_KEY="sk-你的-key"
```

再重新啟動：

```bash
uvicorn proxy:app --host 127.0.0.1 --port 5000
```

---

### 問題 3：FastAPI 回 403 Forbidden

若錯誤內容包含 nginx 403，代表 FastAPI Proxy 轉送到 inner-medusa 時被後端 nginx 擋掉。

請確認後端 endpoint 是：

```text
https://inner-medusa.genai.nchc.org.tw/v1/chat/completions
```

不要設定成：

```text
https://inner-medusa.genai.nchc.org.tw/v1/responses
```

因為目前已知 `/v1/responses` 會被 nginx 回 403。

---

## 15. 可選：建立啟動腳本

本專案提供兩個啟動腳本以應對不同的後端環境（腳本會自動檢測本地虛擬環境，若不存在會自動使用 `uv` 建立並安裝套件）：

### 15.1 Inner-Medusa 後端啟動腳本 (`start_proxy_inner.sh`)
```bash
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

uvicorn proxy:app --host 127.0.0.1 --port 5000
```

### 15.2 Portal 後端啟動腳本 (`start_proxy_portal.sh`)
```bash
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
export BACKEND_API_KEY=$(grep -E "^PORTAL_API_KEY=" .env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
export BACKEND_CHAT_URL="https://portal.genai.nchc.org.tw/api/v1/chat/completions"

uvicorn proxy:app --host 127.0.0.1 --port 5000
```

設定執行權限：
```bash
chmod +x start_proxy_inner.sh start_proxy_portal.sh
```

之後只要執行對應的腳本即可啟動服務：
```bash
./start_proxy_inner.sh
# 或
./start_proxy_portal.sh
```

---

## 16. 安全建議

若只在本機使用，建議維持：

```text
127.0.0.1:5000
```

不要開成：

```text
0.0.0.0:5000
```

避免外部電腦直接連入。

---

## 17. 完整啟動與快速上手流程摘要

以下是全新部署或重新啟動的完整極速上手指南：

### 第一步：複製專案與準備環境
1. **複製 GitHub 儲存庫**：
   ```bash
   git clone https://github.com/gemini960114/claude-code-openai-proxy.git
   cd claude-code-openai-proxy
   ```
2. **複製並設定 `.env` 金鑰檔案**：
   ```bash
   cp .env.example .env
   ```
   *請使用文字編輯器打開新生成的 `.env` 檔案，填入你的實際金鑰（`INNER_MEDUSA_API_KEY` 或 `PORTAL_API_KEY`）。*

### 第二步：執行啟動腳本
3. **賦予執行權限並執行啟動腳本**（腳本會自動檢測並初始化虛擬環境及套件）：
   ```bash
   chmod +x start_proxy_portal.sh start_proxy_inner.sh
   ./start_proxy_portal.sh   # 啟動 Portal 閘道服務
   # 或
   ./start_proxy_inner.sh    # 啟動 Inner-Medusa 服務
   ```
   *看到 `Uvicorn running on http://127.0.0.1:5000` 表示啟動成功，保持該視窗不要關閉。*

### 第三步：一般對話測試 (Curl 檢驗)
4. **另開一個終端機視窗進行測試**：
   - **測試健康度檢查**：
     ```bash
     curl -i "http://127.0.0.1:5000/health"
     ```
   - **測試 API 訊息對話**：
     ```bash
     curl -i "http://127.0.0.1:5000/v1/messages" -H "x-api-key: anything" -H "Content-Type: application/json" -H "anthropic-version: 2023-06-01" --data-raw '{"model":"gemma-4-31B-it","max_tokens":128,"messages":[{"role":"user","content":"hello"}]}'
     ```

### 第四步：啟動 Claude Code 進行工作
5. **在另一個終端機視窗中啟動 Claude**：
   ```bash
   claude --debug
   ```

---

## 18. 結論

此 FastAPI Proxy 方案可讓 Claude Code 使用本機 `/v1/messages`，再由 Proxy 轉換為 OpenAI-compatible `/v1/chat/completions`。
