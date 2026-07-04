import json
import os
import re
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

BACKEND_API_KEY = os.environ.get("BACKEND_API_KEY", "")
print(
    f"[proxy] BACKEND_API_KEY loaded: {'yes' if BACKEND_API_KEY else 'no'}",
    flush=True,
)

BACKEND_CHAT_URL = os.environ.get(
    "BACKEND_CHAT_URL",
    "https://portal.genai.nchc.org.tw/api/v1/chat/completions",
)
MODEL_CONFIG_PATH = Path(
    os.environ.get("MODEL_CONFIG_PATH", Path(__file__).with_name("models_inner.json"))
)

# Conservative compatibility switches.
ENABLE_DOCUMENT_PART = os.environ.get("ENABLE_DOCUMENT_PART", "true").lower() == "true"
FORWARD_EXTRA_PARAMS = os.environ.get("FORWARD_EXTRA_PARAMS", "false").lower() == "true"
STREAM_TOOLS = os.environ.get("STREAM_TOOLS", "false").lower() == "true"
STREAM_INCLUDE_USAGE = os.environ.get("STREAM_INCLUDE_USAGE", "true").lower() == "true"
BACKEND_TIMEOUT_SECONDS = float(os.environ.get("BACKEND_TIMEOUT_SECONDS", "600"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reuse one pooled client for the process lifetime instead of opening a new
    # TCP/TLS connection to the backend on every request.
    app.state.http_client = httpx.AsyncClient(timeout=BACKEND_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)


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


def classify_http_error(status_code: int) -> str:
    """Map an HTTP status from the backend to an Anthropic error.type."""
    if status_code in (401, 403):
        return "authentication_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 400:
        return "invalid_request_error"
    if status_code >= 500:
        return "api_error"
    return "api_error"


def safe_preview(value: Any, limit: int = 2000) -> str:
    """Return a short, log-safe preview without dumping huge base64 payloads."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    # Avoid logging very large inline media payloads.
    text = re.sub(r"data:[^;]+;base64,[A-Za-z0-9+/=]{200,}", "[base64 omitted]", text)
    return text[:limit]


def log_backend_error(status_code: int, response_text: str, requested_model: str, backend_model: str) -> None:
    print(
        "[proxy] Backend error\n"
        f"  status={status_code}\n"
        f"  requested_model={requested_model}\n"
        f"  backend_model={backend_model}\n"
        f"  body={safe_preview(response_text)}",
        flush=True,
    )


def log_exception(prefix: str, exc: BaseException, requested_model: str | None = None, backend_model: str | None = None) -> None:
    print(
        f"[proxy] {prefix}: {type(exc).__name__}: {exc}\n"
        f"requested_model={requested_model}\n"
        f"backend_model={backend_model}\n"
        f"{traceback.format_exc()}",
        flush=True,
    )


def extract_text_from_anthropic_content(content: Any) -> str:
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


def stringify_tool_result_content(content: Any) -> str:
    """Anthropic tool_result content can be a string or block array. OpenAI tool messages require a string."""
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
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
        return "\n".join(p for p in parts if p)

    if content is None:
        return ""
    return str(content)


def anthropic_image_to_data_url(block: dict) -> str | None:
    """Anthropic image content block, base64 or URL source, to OpenAI image_url."""
    source = block.get("source") or {}
    source_type = source.get("type")
    if source_type == "base64":
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        if data:
            return f"data:{media_type};base64,{data}"
    elif source_type == "url":
        return source.get("url")
    return None


def anthropic_document_to_file_part(block: dict) -> dict | None:
    """Anthropic document/PDF content block to OpenAI-compatible file content part."""
    source = block.get("source") or {}
    source_type = source.get("type")
    filename = block.get("title") or "document.pdf"

    if source_type == "base64":
        media_type = source.get("media_type", "application/pdf")
        data = source.get("data", "")
        if not data:
            return None
        return {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:{media_type};base64,{data}",
            },
        }
    if source_type == "url":
        url = source.get("url")
        if not url:
            return None
        return {"type": "file", "file": {"filename": filename, "file_data": url}}
    return None


def convert_tools(anthropic_tools: Any) -> list[dict]:
    """Anthropic tools -> OpenAI function tools. input_schema maps to parameters."""
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


def convert_tool_choice(tool_choice: Any) -> Any:
    """Anthropic tool_choice -> OpenAI tool_choice."""
    if tool_choice is None:
        return None
    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        if choice_type == "auto":
            return "auto"
        if choice_type == "any":
            return "required"
        if choice_type == "none":
            return "none"
        if choice_type == "tool" and tool_choice.get("name"):
            return {"type": "function", "function": {"name": tool_choice.get("name")}}
    return None


def validate_and_fix_tool_message_order(messages: list[dict]) -> list[dict]:
    """Keep legal tool messages and downgrade orphan tool results to user text.

    OpenAI-compatible APIs usually require role=tool messages to immediately follow an
    assistant message containing a matching tool_calls id. This keeps valid cases such
    as assistant tool_use -> user tool_result + text, and prevents backend 400 errors
    for orphaned tool_result blocks.
    """
    fixed: list[dict] = []
    pending_tool_call_ids: set[str] = set()

    for msg in messages:
        role = msg.get("role")

        if role == "assistant":
            fixed.append(msg)
            pending_tool_call_ids.clear()
            for call in msg.get("tool_calls") or []:
                if isinstance(call, dict):
                    call_id = call.get("id")
                    if call_id:
                        pending_tool_call_ids.add(call_id)
            continue

        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id and tool_call_id in pending_tool_call_ids:
                fixed.append(msg)
                pending_tool_call_ids.discard(tool_call_id)
            else:
                fixed.append(
                    {
                        "role": "user",
                        "content": "[Tool result without matching tool call]\n"
                        + str(msg.get("content", "")),
                    }
                )
            continue

        fixed.append(msg)
        if role in ("user", "system"):
            pending_tool_call_ids.clear()

    return fixed


def anthropic_messages_to_openai(payload: dict) -> dict:
    messages: list[dict] = []

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

        # Simple string content.
        if isinstance(content, str):
            out_role = role if role in ("user", "assistant") else "user"
            messages.append({"role": out_role, "content": content})
            continue

        if not isinstance(content, list):
            continue

        if role == "assistant":
            text_parts = []
            tool_calls = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id") or f"toolu_{uuid.uuid4().hex}",
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        }
                    )
            text = "\n".join(t for t in text_parts if t)
            assistant_msg = {"role": "assistant", "content": text if text else None}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
        else:
            # User turns may include tool_result, text, image, and document blocks.
            content_parts = []
            tool_messages = []
            for block in content:
                if isinstance(block, str):
                    content_parts.append({"type": "text", "text": block})
                    continue
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "tool_result":
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": stringify_tool_result_content(block.get("content", "")),
                        }
                    )
                elif block_type == "text":
                    content_parts.append({"type": "text", "text": block.get("text", "")})
                elif block_type == "image":
                    image_url = anthropic_image_to_data_url(block)
                    if image_url:
                        content_parts.append({"type": "image_url", "image_url": {"url": image_url}})
                elif block_type == "document":
                    if ENABLE_DOCUMENT_PART:
                        file_part = anthropic_document_to_file_part(block)
                        if file_part:
                            content_parts.append(file_part)
                    else:
                        filename = block.get("title") or "document.pdf"
                        content_parts.append(
                            {"type": "text", "text": f"[PDF document omitted: {filename}]"}
                        )
                elif "text" in block:
                    content_parts.append({"type": "text", "text": block.get("text", "")})

            # Keep tool result immediately after the assistant tool_calls turn when possible.
            messages.extend(tool_messages)

            has_media = any(p.get("type") != "text" for p in content_parts)
            if has_media:
                if content_parts:
                    messages.append({"role": "user", "content": content_parts})
            else:
                text = "\n".join(p.get("text", "") for p in content_parts if p.get("text"))
                if text:
                    messages.append({"role": "user", "content": text})

    requested_model = payload.get("model", DEFAULT_MODEL)
    backend_model = map_model(requested_model)

    result = {
        "model": backend_model,
        "messages": validate_and_fix_tool_message_order(messages),
        "max_tokens": payload.get("max_tokens", 1024),
        "temperature": payload.get("temperature", 0.7),
        "stream": bool(payload.get("stream", False)),
    }

    if result["stream"] and STREAM_INCLUDE_USAGE:
        # Ask the backend to emit a final usage chunk; without this most
        # OpenAI-compatible backends never report token counts while streaming.
        result["stream_options"] = {"include_usage": True}

    stop_sequences = payload.get("stop_sequences")
    if stop_sequences:
        result["stop"] = stop_sequences

    if payload.get("top_p") is not None:
        result["top_p"] = payload["top_p"]

    if FORWARD_EXTRA_PARAMS:
        for key in ["presence_penalty", "frequency_penalty", "seed", "top_k"]:
            if payload.get(key) is not None:
                result[key] = payload[key]

    tools = payload.get("tools")
    if tools:
        converted_tools = convert_tools(tools)
        if converted_tools:
            result["tools"] = converted_tools
            tool_choice = convert_tool_choice(payload.get("tool_choice"))
            if tool_choice is not None:
                result["tool_choice"] = tool_choice

    return result


def parse_tool_arguments(raw_args: Any) -> dict:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            return json.loads(raw_args)
        except (ValueError, TypeError):
            return {}
    return {}


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
        content.append(
            {
                "type": "tool_use",
                "id": call.get("id") or f"toolu_{uuid.uuid4().hex}",
                "name": fn.get("name", ""),
                "input": parse_tool_arguments(fn.get("arguments", "")),
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
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def sse_event(event: str | None, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    if event:
        return f"event: {event}\ndata: {payload}\n\n"
    return f"data: {payload}\n\n"


def make_message_start(message_id: str, model: str) -> dict:
    return {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }


async def stream_backend_to_anthropic(
    client: httpx.AsyncClient,
    openai_payload: dict,
    headers: dict,
    requested_model: str,
    backend_model: str,
) -> AsyncGenerator[str, None]:
    """Convert OpenAI-compatible SSE streaming into Anthropic Messages SSE.

    Supports text deltas and, when the backend emits delta.tool_calls (only reachable
    when STREAM_TOOLS=true at the call site), tool_use content blocks streamed via
    input_json_delta. Content blocks are opened lazily and closed strictly in order,
    matching the Anthropic protocol's no-interleaving requirement. This assumes each
    tool call's argument fragments arrive contiguously before the next tool call
    starts, which holds for OpenAI itself and the OpenAI-compatible backends this
    proxy has been tested against.
    """
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    message_started = False
    next_index = 0
    open_kind: str | None = None  # None | "text" | "tool"
    open_tool_call_index: int | None = None
    text_anthropic_index: int | None = None
    tool_anthropic_index: dict[int, int] = {}
    stop_reason = "end_turn"
    output_tokens = 0

    try:
        async with client.stream(
            "POST",
            BACKEND_CHAT_URL,
            headers=headers,
            json=openai_payload,
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                body_text = body.decode("utf-8", errors="replace")
                log_backend_error(resp.status_code, body_text, requested_model, backend_model)
                yield sse_event(
                    "error",
                    {
                        "type": "error",
                        "error": {
                            "type": classify_http_error(resp.status_code),
                            "message": f"Backend error {resp.status_code}: {safe_preview(body_text, 1000)}",
                        },
                    },
                )
                yield sse_event(None, "[DONE]")
                return

            yield sse_event("message_start", make_message_start(message_id, requested_model))
            message_started = True
            yield sse_event("ping", {"type": "ping"})

            async for raw_line in resp.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                else:
                    # Ignore non-data SSE fields such as event:, id:, retry:.
                    continue

                if line == "[DONE]":
                    break

                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                usage = chunk.get("usage") or {}
                if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
                    output_tokens = usage["completion_tokens"]

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    stop_reason = STOP_REASON_MAP.get(finish_reason, "end_turn")

                delta = choice.get("delta") or {}

                delta_text = delta.get("content")
                if delta_text:
                    if open_kind == "tool":
                        yield sse_event(
                            "content_block_stop",
                            {"type": "content_block_stop", "index": tool_anthropic_index[open_tool_call_index]},
                        )
                        open_kind = None
                        open_tool_call_index = None
                    if open_kind != "text":
                        text_anthropic_index = next_index
                        next_index += 1
                        yield sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": text_anthropic_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )
                        open_kind = "text"
                    yield sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": text_anthropic_index,
                            "delta": {"type": "text_delta", "text": delta_text},
                        },
                    )

                delta_tool_calls = delta.get("tool_calls")
                if delta_tool_calls:
                    stop_reason = "tool_use"
                    for tc in delta_tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        tc_index = tc.get("index", 0)
                        fn = tc.get("function") or {}

                        if tc_index not in tool_anthropic_index:
                            if open_kind == "text":
                                yield sse_event(
                                    "content_block_stop",
                                    {"type": "content_block_stop", "index": text_anthropic_index},
                                )
                                open_kind = None
                            elif open_kind == "tool" and open_tool_call_index != tc_index:
                                yield sse_event(
                                    "content_block_stop",
                                    {"type": "content_block_stop", "index": tool_anthropic_index[open_tool_call_index]},
                                )

                            anthropic_index = next_index
                            next_index += 1
                            tool_anthropic_index[tc_index] = anthropic_index
                            yield sse_event(
                                "content_block_start",
                                {
                                    "type": "content_block_start",
                                    "index": anthropic_index,
                                    "content_block": {
                                        "type": "tool_use",
                                        "id": tc.get("id") or f"toolu_{uuid.uuid4().hex}",
                                        "name": fn.get("name", ""),
                                        "input": {},
                                    },
                                },
                            )
                            open_kind = "tool"
                            open_tool_call_index = tc_index

                        arguments_fragment = fn.get("arguments")
                        if arguments_fragment:
                            yield sse_event(
                                "content_block_delta",
                                {
                                    "type": "content_block_delta",
                                    "index": tool_anthropic_index[tc_index],
                                    "delta": {"type": "input_json_delta", "partial_json": arguments_fragment},
                                },
                            )

            if open_kind == "text":
                yield sse_event("content_block_stop", {"type": "content_block_stop", "index": text_anthropic_index})
            elif open_kind == "tool":
                yield sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": tool_anthropic_index[open_tool_call_index]},
                )
            elif next_index == 0:
                # Nothing was ever produced. Emit an empty text block so the
                # response shape matches the non-streaming path.
                yield sse_event(
                    "content_block_start",
                    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                )
                yield sse_event("content_block_stop", {"type": "content_block_stop", "index": 0})

            yield sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                },
            )
            yield sse_event("message_stop", {"type": "message_stop"})
            yield sse_event(None, "[DONE]")
    except Exception as exc:
        log_exception("Streaming error", exc, requested_model, backend_model)
        if not message_started:
            yield sse_event("message_start", make_message_start(message_id, requested_model))
            message_started = True
        if open_kind == "text":
            yield sse_event("content_block_stop", {"type": "content_block_stop", "index": text_anthropic_index})
        elif open_kind == "tool":
            yield sse_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": tool_anthropic_index[open_tool_call_index]},
            )
        yield sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(exc)}},
        )
        yield sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )
        yield sse_event("message_stop", {"type": "message_stop"})
        yield sse_event(None, "[DONE]")


def estimate_input_tokens(payload: dict) -> int:
    text_parts = []
    for key in ["system", "messages", "tools"]:
        value = payload.get(key)
        if value:
            text_parts.append(json.dumps(value, ensure_ascii=False, default=str))
    raw_text = "\n".join(text_parts)
    # Conservative compatibility estimate. This is not for billing or exact context accounting.
    return max(1, len(raw_text) // 2)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "claude-message-proxy",
        "backend": BACKEND_CHAT_URL,
        "model_config": str(MODEL_CONFIG_PATH),
        "default_model": DEFAULT_MODEL,
        "enable_document_part": ENABLE_DOCUMENT_PART,
        "forward_extra_params": FORWARD_EXTRA_PARAMS,
        "stream_tools": STREAM_TOOLS,
        "stream_include_usage": STREAM_INCLUDE_USAGE,
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


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    payload = await request.json()
    return {"input_tokens": estimate_input_tokens(payload)}


@app.post("/v1/messages")
async def messages(request: Request):
    if not BACKEND_API_KEY:
        raise HTTPException(status_code=500, detail="BACKEND_API_KEY is not set")

    payload = await request.json()
    requested_model = payload.get("model", DEFAULT_MODEL)
    backend_model = map_model(requested_model)
    openai_payload = anthropic_messages_to_openai(payload)

    # Text streaming is always safe. Tool-call streaming is opt-in via STREAM_TOOLS
    # since it depends on the backend emitting well-formed, contiguous tool_calls
    # deltas; fall back to non-streaming so tool use stays reliable by default.
    if openai_payload.get("stream") and openai_payload.get("tools") and not STREAM_TOOLS:
        print(
            "[proxy] stream=true with tools detected; falling back to non-streaming "
            "because STREAM_TOOLS=false",
            flush=True,
        )
        openai_payload["stream"] = False
        openai_payload.pop("stream_options", None)

    print(
        f"[proxy] requested_model={requested_model} backend_model={backend_model} "
        f"stream={openai_payload.get('stream', False)} messages={len(openai_payload.get('messages', []))}",
        flush=True,
    )

    headers = {
        "Authorization": f"Bearer {BACKEND_API_KEY}",
        "Content-Type": "application/json",
    }

    client: httpx.AsyncClient = request.app.state.http_client

    if openai_payload.get("stream"):
        stream_headers = {**headers, "Accept": "text/event-stream"}
        return StreamingResponse(
            stream_backend_to_anthropic(client, openai_payload, stream_headers, requested_model, backend_model),
            media_type="text/event-stream",
        )

    try:
        resp = await client.post(BACKEND_CHAT_URL, headers=headers, json=openai_payload)

        if resp.status_code >= 400:
            log_backend_error(resp.status_code, resp.text, requested_model, backend_model)
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        data = resp.json()
        return JSONResponse(openai_to_anthropic(data, requested_model))

    except HTTPException:
        raise
    except Exception as exc:
        log_exception("Request error", exc, requested_model, backend_model)
        raise HTTPException(status_code=500, detail=f"Proxy request error: {exc}") from exc
