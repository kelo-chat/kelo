"""Which providers can be shown a picture, and the one helper the transports share to turn the app's image parts into their own.

The agent loop sends a picture as an OpenAI-style user message part: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}.
- the Claude CLI (DirectSDK) and the OpenAI-style chat endpoints take that as it is;
- the Anthropic Messages, Bedrock Converse and Responses transports convert it (see their convert functions);
- the agy CLI passes text only (its prompt renderer drops every non-text part), so it is never sent pictures;
- other OpenAI-compatible endpoints may or may not accept images depending on the model, and a text-only model answers an image with an
  error that would fail the whole turn: they are treated as text-only until known otherwise.
"""

from __future__ import annotations

import base64
from typing import Any

from .registry import get_provider

VISION_TRANSPORTS = {"cli_claude", "anthropic_messages", "bedrock_converse"}
VISION_PROVIDERS = {"openai", "openai-codex", "gemini", "vertex", "xai", "anthropic", "bedrock"}


def supports_images(provider: str) -> bool:
    try:
        p = get_provider(provider)
    except KeyError:
        return False
    if p.transport == "cli_agy":
        return False
    if p.name == "anthropic" or p.name in VISION_PROVIDERS:
        return True
    return p.transport == "cli_claude" or (p.transport in VISION_TRANSPORTS and p.name in ("anthropic", "bedrock"))


def data_url_parts(url: str) -> tuple[str, str] | None:
    """('image/jpeg', base64 text) of a data: URL, or None for anything else (a remote URL is never fetched by a transport)."""
    if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
        return None
    media, data = url[5:].split(";base64,", 1)
    return media, data


def image_part_url(part: Any) -> str | None:
    if isinstance(part, dict) and part.get("type") == "image_url":
        inner = part.get("image_url")
        return inner.get("url") if isinstance(inner, dict) else inner if isinstance(inner, str) else None
    return None


def anthropic_blocks(content: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in content:
        url = image_part_url(part)
        if url is not None:
            got = data_url_parts(url)
            out.append({"type": "image", "source": {"type": "base64", "media_type": got[0], "data": got[1]}} if got
                       else {"type": "text", "text": "[image not attached: only pictures sent with the message can be shown]"})
        elif isinstance(part, dict) and part.get("type") == "text":
            out.append({"type": "text", "text": str(part.get("text") or "")})
        else:
            out.append(part)
    return out


def bedrock_blocks(content: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in content:
        url = image_part_url(part)
        if url is not None:
            got = data_url_parts(url)
            fmt = got[0].split("/")[-1].replace("jpg", "jpeg") if got else ""
            out.append({"image": {"format": fmt, "source": {"bytes": base64.b64decode(got[1])}}} if got and fmt in ("jpeg", "png", "webp", "gif")
                       else {"text": "[image not attached]"})
        elif isinstance(part, dict) and part.get("type") == "text":
            out.append({"text": str(part.get("text") or "")})
        else:
            out.append(part)
    return out


def responses_parts(content: list[Any], role: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in content:
        url = image_part_url(part)
        if url is not None:
            out.append({"type": "input_image", "image_url": url})
        elif isinstance(part, dict) and part.get("type") == "text":
            out.append({"type": "output_text" if role == "assistant" else "input_text", "text": str(part.get("text") or "")})
        else:
            out.append(part)
    return out
