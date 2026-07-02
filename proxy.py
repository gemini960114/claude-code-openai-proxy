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
