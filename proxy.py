import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")
BACKEND_CHAT_URL = os.environ.get(
    "BACKEND_CHAT_URL",
    "https://portal.genai.nchc.org.tw/api/v1/chat/completions",
)
MODEL_CONFIG_PATH = Path(
    os.environ.get("MODEL_CONFIG_PATH", Path(__file__).with_name("models_inner.json"))
)

def load_model_config() -> dict:
    if not MODEL_CONFIG_PATH.exists():
        raise RuntimeError(f"model config file not found: {MODEL_CONFIG_PATH}")

    with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    if not isinstance(config.get("models"), list) or not config["models"]:
        raise RuntimeError(f"{MODEL_CONFIG_PATH.name} must contain a non-empty models array")

    return config


MODEL_CONFIG = load_model_config()
DEFAULT_MODEL = MODEL_CONFIG.get("default_model", "claude-haiku-4-5")


def model_entries() -> list[dict]:
    return MODEL_CONFIG.get("models", [])


def normalize_model_id(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model)


def map_model(model: str) -> str:
    normalized_model = normalize_model_id(model)
    for entry in model_entries():
        names = [entry.get("id"), *entry.get("aliases", [])]
        if model in names or normalized_model in names:
            return entry.get("backend_model", model)
    return model


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

    requested_model = payload.get("model", DEFAULT_MODEL)
    backend_model = map_model(requested_model)

    return {
        "model": backend_model,
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
        "model_config": str(MODEL_CONFIG_PATH),
        "default_model": DEFAULT_MODEL,
    }


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {
                "id": entry["id"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": entry.get("owned_by", "inner-medusa"),
                "display_name": entry.get("display_name", entry.get("backend_model", entry["id"])),
            }
            for entry in model_entries()
            if entry.get("id")
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

    requested_model = payload.get("model", DEFAULT_MODEL)
    backend_model = map_model(requested_model)

    openai_payload = anthropic_messages_to_openai(payload)

    print(
        f"[proxy] requested_model={requested_model} backend_model={backend_model}",
        flush=True,
    )

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

    return JSONResponse(openai_to_anthropic(data, requested_model))
