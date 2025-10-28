"""Gemini-backed helper to build Tuya scene payloads from natural language."""
from __future__ import annotations

import json
import os
import re
from threading import Lock
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(".env")

_BUILDER_PROMPT_PATH = "configs/tuya_scene_builder_prompt.txt"
_DEFAULT_MODEL = os.getenv("GEMINI_SCENE_BUILDER_MODEL", "gemini-2.5-flash")
_RESPONSE_MIME_TYPE = "application/json"

_BUILDER_CLIENT: Optional[genai.Client] = None
_BUILDER_PROMPT: Optional[str] = None
_BUILDER_LOCK = Lock()


class SceneBuilderError(RuntimeError):
    """Raised when the scene builder cannot produce a valid payload."""


def _load_prompt() -> str:
    global _BUILDER_PROMPT
    if _BUILDER_PROMPT:
        return _BUILDER_PROMPT
    try:
        with open(_BUILDER_PROMPT_PATH, encoding="utf-8") as handle:
            _BUILDER_PROMPT = handle.read()
            return _BUILDER_PROMPT
    except FileNotFoundError as exc:  # pragma: no cover - configuration error
        raise SceneBuilderError(
            f"Scene builder prompt not found at '{_BUILDER_PROMPT_PATH}'."
        ) from exc


def _get_client() -> genai.Client:
    global _BUILDER_CLIENT
    if _BUILDER_CLIENT is not None:
        return _BUILDER_CLIENT
    with _BUILDER_LOCK:
        if _BUILDER_CLIENT is not None:
            return _BUILDER_CLIENT
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise SceneBuilderError("GEMINI_API_KEY is not configured.")
        _BUILDER_CLIENT = genai.Client(api_key=api_key)
        _load_prompt()
        return _BUILDER_CLIENT


def _normalise_payload_text(text: str) -> str:
    """Extract the JSON object from the model output."""
    if not text:
        raise SceneBuilderError("Scene builder returned an empty response.")

    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove code fences if the model slipped
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()

    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace < first_brace:
        raise SceneBuilderError("Scene builder response does not contain JSON.")
    return stripped[first_brace : last_brace + 1]


def _parse_payload(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SceneBuilderError(f"Failed to parse scene payload JSON: {exc}") from exc


def prewarm_scene_builder() -> None:
    """Initialise the Gemini client ahead of time to reduce first-call latency."""
    try:
        _get_client()
    except Exception as exc:  # pragma: no cover - network failure
        print(f"⚠️ Could not prewarm scene builder: {exc}")


def build_scene_payload(
    *,
    instructions: str,
    context: Dict[str, Any],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a Tuya scene payload from natural language instructions."""
    if not instructions:
        raise SceneBuilderError("Instructions are required to build a scene payload.")

    payload_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    prompt_body = (
        "Contexto disponível (use os IDs apenas dentro do payload final):\n"
        f"{payload_context}\n\n"
        "Pedido do usuário:\n"
        f"{instructions.strip()}"
    )

    client = _get_client()
    try:
        chat = client.chats.create(
            model=model or _DEFAULT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=_load_prompt(),
                response_mime_type=_RESPONSE_MIME_TYPE,
            ),
        )
        response = chat.send_message(message=prompt_body)
    except Exception as exc:  # pragma: no cover - network failure
        raise SceneBuilderError(f"Scene builder request failed: {exc}") from exc

    text = getattr(response, "text", "") or ""
    if not text and getattr(response, "candidates", None):
        parts = []
        for candidate in response.candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                if getattr(part, "text", None):
                    parts.append(part.text)
        text = "\n".join(parts).strip()

    payload_text = _normalise_payload_text(text)
    return {
        "payload": _parse_payload(payload_text),
        "raw_response": text,
    }
