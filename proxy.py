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
print(f"[proxy] BACKEND_API_KEY loaded: length={len(BACKEND_API_KEY)} prefix={BACKEND_API_KEY[:6]}", flush=True)
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


# OpenAI finish_reason -> Anthropic stop_reason
STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


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


def stringify_tool_result_content(content):
    """Anthropic 的 tool_result content 可為字串或區塊陣列，OpenAI 的 tool 訊息只吃字串。"""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append("[image omitted]")
                elif "text" in item:
                    parts.append(item.get("text", ""))
        return "\n".join(parts)

    if content is None:
        return ""
    return str(content)


def convert_tools(anthropic_tools):
    """Anthropic tools -> OpenAI tools。input_schema 對應 parameters。"""
    openai_tools = []
    for tool in anthropic_tools or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not name:
            continue
        schema = tool.get("input_schema") or {"type": "object", "properties": {}}
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return openai_tools


def convert_tool_choice(tool_choice):
    """Anthropic tool_choice -> OpenAI tool_choice。"""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, dict):
        t = tool_choice.get("type")
        if t == "auto":
            return "auto"
        if t == "any":
            return "required"
        if t == "none":
            return "none"
        if t == "tool" and tool_choice.get("name"):
            return {"type": "function", "function": {"name": tool_choice.get("name")}}
    return None


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
        content = msg.get("content", "")

        # 純字串內容：直接沿用
        if isinstance(content, str):
            out_role = role if role in ("user", "assistant") else "user"
            messages.append({"role": out_role, "content": content})
            continue

        if not isinstance(content, list):
            continue

        if role == "assistant":
            # 助理回合可能同時含 text 與 tool_use
            text_parts = []
            tool_calls = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(
                                    block.get("input", {}), ensure_ascii=False
                                ),
                            },
                        }
                    )
            text = "\n".join(t for t in text_parts if t)
            assistant_msg = {"role": "assistant", "content": text if text else None}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
        else:
            # 使用者回合可能含 tool_result（工具執行結果）與一般 text
            text_parts = []
            tool_messages = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                    continue
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": stringify_tool_result_content(
                                block.get("content", "")
                            ),
                        }
                    )
                elif btype == "text":
                    text_parts.append(block.get("text", ""))
                elif "text" in block:
                    text_parts.append(block.get("text", ""))
            # OpenAI 要求 tool 訊息緊接在帶 tool_calls 的 assistant 訊息之後
            messages.extend(tool_messages)
            text = "\n".join(t for t in text_parts if t)
            if text:
                messages.append({"role": "user", "content": text})

    requested_model = payload.get("model", DEFAULT_MODEL)
    backend_model = map_model(requested_model)

    result = {
        "model": backend_model,
        "messages": messages,
        "max_tokens": payload.get("max_tokens", 1024),
        "temperature": payload.get("temperature", 0.7),
        "stream": False,
    }

    stop_sequences = payload.get("stop_sequences")
    if stop_sequences:
        result["stop"] = stop_sequences

    tools = payload.get("tools")
    if tools:
        converted_tools = convert_tools(tools)
        if converted_tools:
            result["tools"] = converted_tools
            tool_choice = convert_tool_choice(payload.get("tool_choice"))
            if tool_choice is not None:
                result["tool_choice"] = tool_choice

    return result


def openai_to_anthropic(openai_resp: dict, model: str) -> dict:
    choice = (openai_resp.get("choices") or [{}])[0]
    message = choice.get("message", {}) or {}
    text = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    finish_reason = choice.get("finish_reason", "stop")

    content = []
    if text:
        content.append({"type": "text", "text": text})

    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function", {}) or {}
        raw_args = fn.get("arguments", "")
        try:
            if isinstance(raw_args, dict):
                parsed_input = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                parsed_input = json.loads(raw_args)
            else:
                parsed_input = {}
        except (ValueError, TypeError):
            parsed_input = {}
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"toolu_{uuid.uuid4().hex}",
                "name": fn.get("name", ""),
                "input": parsed_input,
            }
        )

    if not content:
        content.append({"type": "text", "text": ""})

    stop_reason = STOP_REASON_MAP.get(finish_reason, "end_turn")
    if tool_calls:
        stop_reason = "tool_use"

    usage = openai_resp.get("usage", {}) or {}

    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
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
        print(f"[proxy] Backend error: status={resp.status_code} body={resp.text}", flush=True)
        raise HTTPException(
            status_code=resp.status_code,
            detail=resp.text,
        )

    data = resp.json()

    return JSONResponse(openai_to_anthropic(data, requested_model))
