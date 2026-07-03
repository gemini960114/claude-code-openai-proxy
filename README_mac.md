# Claude Code 外接 OpenAI-Compatible API：FastAPI Proxy (macOS)

這個專案提供一個本機 FastAPI Proxy，讓 Claude Code 使用 Anthropic-compatible `/v1/messages`，再由 Proxy 轉換成後端 OpenAI-compatible `/v1/chat/completions`。

```text
Claude Code
-> http://127.0.0.1:5000/v1/messages
-> FastAPI Proxy
-> BACKEND_CHAT_URL /v1/chat/completions
-> 後端模型
```

模型對應集中在 `models_inner.json`（Inner 用）與 `models_portal.json`（Portal 用）。Claude Code 的 `settings.json` 負責連線到本機 Proxy，並設定 `/model` 選單中 Haiku、Sonnet、Opus 三個槽位要顯示的後端模型名稱。

---

## 檔案說明

- `proxy.py`：唯一的 Proxy 主程式，已整合原本 v1 的模型對應邏輯。
- `models_inner.json` / `models_portal.json`：模型設定檔，對外顯示後端真實模型名稱，並用 aliases 相容 Claude Code 送來的 model id。
- `.env`：本機 API key 設定，不要提交到 Git。
- `start_proxy_inner.sh`：使用 Inner-Medusa endpoint 啟動。
- `start_proxy_portal.sh`：使用 Portal endpoint 啟動。
- `claude_settings_inner_example.json` / `claude_settings_portal_example.json`：Claude Code settings 範例。

---

## 環境需求

- macOS 12+
- Python 3.10+
- uv
- Claude Code
- Terminal / zsh / bash

確認 `uv`：

```bash
uv --version
```

---

## 安裝

```bash
git clone https://github.com/gemini960114/claude-code-openai-proxy.git
cd claude-code-openai-proxy
cp .env.example .env
```

編輯 `.env`，填入你要使用的 API key：

```text
INNER_MEDUSA_API_KEY="sk-你的-inner-medusa-key"
PORTAL_API_KEY="sk-你的-portal-key"
```

`.env` 已被 `.gitignore` 排除，請不要把真實金鑰提交到 Git。

---

## 啟動 Proxy

Portal：

```bash
./start_proxy_portal.sh
```

Inner-Medusa：

```bash
./start_proxy_inner.sh
```

腳本會自動建立 `.venv` 並安裝必要套件：

```text
fastapi
uvicorn
httpx
```

看到以下訊息代表啟動成功：

```text
Uvicorn running on http://127.0.0.1:5000
```

---

## Claude Code 設定

編輯：

```text
~/.claude/settings.json
```

建議使用以下設定。`ANTHROPIC_DEFAULT_*_MODEL_NAME` 會讓 Claude Code 的 `/model` 選單顯示後端真實模型名稱；`models_inner.json` / `models_portal.json` 則負責 Proxy 端的模型對應。

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
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
    "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
    "ANTHROPIC_MODEL": "GLM-5.2",
    "CLAUDE_CODE_DISABLE_THINKING": "1",
    "ANTHROPIC_DISABLE_THINKING": "1",
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"
  },
  "model": "haiku"
}
```

---

## 模型設定

### 1. Inner 設定檔 (`models_inner.json`) 範例：

```json
{
  "default_model": "GLM-5.2",
  "models": [
    {
      "id": "MiniMax-M2.7",
      "backend_model": "MiniMax-M2.7",
      "display_name": "MiniMax-M2.7",
      "aliases": [
        "haiku",
        "claude-haiku-4-5",
        "minimax-m2.7",
        "minimax-m2",
        "m2.7"
      ]
    },
    {
      "id": "MiniMax-M3",
      "backend_model": "MiniMax-M3",
      "display_name": "MiniMax-M3",
      "aliases": [
        "sonnet",
        "claude-sonnet-4-6",
        "minimax-m3",
        "m3"
      ]
    },
    {
      "id": "GLM-5.2",
      "backend_model": "GLM-5.2",
      "display_name": "GLM-5.2",
      "aliases": [
        "opus",
        "claude-opus-4-7"
      ]
    },
    {
      "id": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "backend_model": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "display_name": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "aliases": [
        "nemotron-ultra",
        "nemotron"
      ]
    },
    {
      "id": "Thanos3.5-397B-A17B",
      "backend_model": "Thanos3.5-397B-A17B",
      "display_name": "Thanos3.5-397B-A17B",
      "aliases": [
        "thanos"
      ]
    },
    {
      "id": "gemma-4-31B-it",
      "backend_model": "gemma-4-31B-it",
      "display_name": "gemma-4-31B-it",
      "aliases": [
        "gemma"
      ]
    }
  ]
}
```

### 2. Portal 設定檔 (`models_portal.json`) 範例：

```json
{
  "default_model": "gemma-4-31B-it",
  "models": [
    {
      "id": "gemma-4-31B-it",
      "backend_model": "gemma-4-31B-it",
      "display_name": "gemma-4-31B-it",
      "aliases": [
        "haiku",
        "claude-haiku-4-5",
        "gemma"
      ]
    },
    {
      "id": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "backend_model": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "display_name": "NVIDIA-Nemotron-3-Ultra-550B-A55B",
      "aliases": [
        "sonnet",
        "claude-sonnet-4-6"
      ]
    },
    {
      "id": "Mistral-Large-3-675B-Instruct-2512",
      "backend_model": "Mistral-Large-3-675B-Instruct-2512",
      "display_name": "Mistral-Large-3-675B-Instruct-2512",
      "aliases": [
        "opus",
        "claude-opus-4-7"
      ]
    },
    {
      "id": "NVIDIA-Nemotron-3-Super-120B-A12B",
      "backend_model": "NVIDIA-Nemotron-3-Super-120B-A12B",
      "display_name": "NVIDIA-Nemotron-3-Super-120B-A12B",
      "aliases": [
        "nemotron-super",
        "super"
      ]
    }
  ]
}
```

### 模型名稱規整化 (Normalization)

Proxy 在比對模型時，會自動裁切掉結尾的 8 位數日期後綴（例如將 `claude-haiku-4-5-20251001` 裁切為 `claude-haiku-4-5` 後再進行比對）。因此 `models_inner.json` 或 `models_portal.json` 中的 `aliases` 欄位不需列出所有帶有日期後綴的完整名稱。

### 新增與自訂模型

要新增模型，例如新增 Mistral 模型：

```json
{
  "id": "Mistral-Large-3-675B-Instruct-2512",
  "backend_model": "Mistral-Large-3-675B-Instruct-2512",
  "display_name": "Mistral-Large-3-675B-Instruct-2512",
  "aliases": ["sonnet-4-8", "claude-sonnet-4-8"]
}
```

> [!NOTE]
> 請避免將已存在的別名（如已被 Nemotron 佔用的 `claude-sonnet-4-6`）重複指定給新模型，以免造成對應衝突。

修改對應的設定檔後重新啟動 Proxy 即可生效。

額外模型不會新增到 Claude Code 的 `/model` 固定選單，但可以用 `--model` 啟動。例如使用 `minimax-m3`：

```bash
claude --model minimax-m3
```

或使用 alias：

```bash
claude --model m3
```

### 進階設定：自訂設定檔路徑

預設會根據啟動腳本載入 `models_inner.json` 或 `models_portal.json`。若直接以 `uvicorn` 啟動 Proxy，可透過在 `.env` 中設定 `MODEL_CONFIG_PATH` 自訂設定檔路徑：

```text
MODEL_CONFIG_PATH="/path/to/your/models_custom.json"
```

---

## 測試

Health check：

```bash
curl -i "http://127.0.0.1:5000/health"
```

模型清單：

```bash
curl -i "http://127.0.0.1:5000/v1/models"
```

訊息測試：

```bash
curl -i "http://127.0.0.1:5000/v1/messages" \
  -H "x-api-key: anything" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  --data-raw '{"model":"GLM-5.2","max_tokens":128,"messages":[{"role":"user","content":"hello"}]}'
```

啟動 Claude Code：

```bash
claude --debug
```

---

## 常見問題

### Claude Code 顯示 Please run /login

確認 `settings.json` 有設定：

```json
"ANTHROPIC_BASE_URL": "http://127.0.0.1:5000"
```

並確認 Proxy 正在執行。

### FastAPI 回 500：BACKEND_API_KEY is not set

代表 `.env` 沒有填 key，或啟動腳本沒有讀到 key。請確認 `.env` 內有對應欄位：

```text
INNER_MEDUSA_API_KEY="sk-..."
PORTAL_API_KEY="sk-..."
```

### 修改設定檔後沒有生效

目前設定檔在 Proxy 啟動時讀取。修改後請重新啟動 `start_proxy_inner.sh` 或 `start_proxy_portal.sh`。

### 後端回 403 Forbidden

若錯誤內容包含 nginx 403，請確認 `BACKEND_CHAT_URL` 是可用的 chat completions endpoint，不要指到 `/v1/responses`。

### 畫面上沒有即時一個字一個字跑出回覆（不支援串流 Streaming）

為了保持架構與格式轉換的單純，本 Proxy 目前強制關閉串流（在向後端發送請求時設定 `"stream": False`），並在獲取後端完整回應後一次性包裝回傳。因此 Claude Code 畫面上不會即時呈現打字效果，而是會在等待一段時間後一次顯示，此為正常運作現象。

---

## 安全建議

本機使用建議維持：

```text
127.0.0.1:5000
```

不要改成：

```text
0.0.0.0:5000
```

除非你已經準備好 HTTPS、API key 驗證、IP allowlist、rate limit 與 access log。
