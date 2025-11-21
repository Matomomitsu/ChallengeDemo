from __future__ import annotations

import asyncio
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

from dotenv import load_dotenv
from integrations.tuya.ai_tools import describe_space

load_dotenv(".env")

DEFAULT_TUYA_SPACE_ID = os.getenv("TUYA_SPACE_ID", "").strip()

_TUYA_CACHE_TTL_SECONDS = float(os.getenv("TUYA_CONTEXT_CACHE_TTL", "300.0"))
_TUYA_BOOTSTRAP_TIMEOUT_SECONDS = float(os.getenv("TUYA_DESCRIBE_TIMEOUT", "3.0"))

_AUTOMATION_KEYWORDS: Sequence[str] = (
    "automation",
    "automacao",
    "automação",
    "scene",
    "cena",
    "smart plug",
    "smartplug",
    "tomada",
    "tuya",
    "device",
    "dispositivo",
    "light",
    "luz",
    "ligar",
    "desligar",
    "home automation",
    "tap-to-run",
    "tap to run",
)


def _normalize_label(value: str) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    return normalized


def _build_lookup(entries: Sequence[Dict[str, Any]], *, name_fields: Sequence[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for entry in entries or []:
        identifier = entry.get("id") or entry.get("rule_id")
        if not identifier:
            continue
        seen: set[str] = set()
        for field in name_fields:
            candidate = entry.get(field)
            if not candidate:
                continue
            label = str(candidate).strip()
            if not label:
                continue
            normalized = _normalize_label(label)
            if not normalized or normalized in seen:
                continue
            lookup[normalized] = identifier
            seen.add(normalized)
    return lookup


@dataclass
class TuyaContextCache:
    space_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    timestamp: float = 0.0
    device_lookup: Dict[str, str] = field(default_factory=dict)
    scene_lookup: Dict[str, str] = field(default_factory=dict)


class TuyaContextManager:
    def __init__(
        self,
        default_space_id: Optional[str],
        *,
        cache_ttl: float = _TUYA_CACHE_TTL_SECONDS,
        bootstrap_timeout: float = _TUYA_BOOTSTRAP_TIMEOUT_SECONDS,
    ) -> None:
        self.default_space_id = default_space_id or ""
        self.cache_ttl = cache_ttl
        self.bootstrap_timeout = bootstrap_timeout
        self._cache = TuyaContextCache()

    def invalidate(self) -> None:
        self._cache = TuyaContextCache()

    def _update_cache(self, space_id: str, payload: Dict[str, Any]) -> None:
        devices = payload.get("devices") or []
        scenes = payload.get("scenes") or []
        self._cache = TuyaContextCache(
            space_id=space_id,
            payload=payload,
            timestamp=time.time(),
            device_lookup=_build_lookup(devices, name_fields=("customName", "name")),
            scene_lookup=_build_lookup(scenes, name_fields=("customName", "name", "display_name")),
        )

    def _is_valid(self, space_id: str) -> bool:
        if not self._cache.payload or self._cache.space_id != space_id:
            return False
        return (time.time() - self._cache.timestamp) < self.cache_ttl

    def get_cached_payload(self, space_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not space_id:
            return None
        if self._is_valid(space_id):
            return self._cache.payload
        return None

    def _format_context(self, payload: Dict[str, Any]) -> str:
        space_id = payload.get("space_id") or self.default_space_id or ""
        devices = payload.get("devices") or []
        scenes = payload.get("scenes") or []

        device_lines = []
        for device in devices:
            friendly = (device.get("customName") or device.get("name") or "Device").strip()
            device_id = device.get("id", "")
            device_lines.append(f"- {friendly} -> {device_id}")
        if not device_lines:
            device_lines.append("- no devices available")

        scene_lines = []
        for scene in scenes:
            friendly = (scene.get("name") or scene.get("display_name") or "Scene").strip()
            rule_id = scene.get("rule_id") or scene.get("id") or ""
            scene_lines.append(f"- {friendly} -> {rule_id}")
        if not scene_lines:
            scene_lines.append("- no scenes registered")

        return "\n".join(
            [
                "[Internal Tuya context - never expose IDs]",
                f"space_id (internal): {space_id}",
                "Known devices:",
                *device_lines,
                "Known scenes:",
                *scene_lines,
                "Use only friendly names in responses; use IDs only inside function calls.",
            ]
        )

    def _resolve_identifier(self, identifier: Any, lookup: Dict[str, str]) -> Any:
        if not isinstance(identifier, str):
            return identifier
        trimmed = identifier.strip()
        if not trimmed:
            return identifier
        normalized = _normalize_label(trimmed)
        resolved = lookup.get(normalized)
        if resolved:
            return resolved
        if trimmed in lookup.values():
            return trimmed
        return identifier

    def resolve_device_identifier(self, identifier: Any) -> Any:
        return self._resolve_identifier(identifier, self._cache.device_lookup)

    def resolve_scene_identifier(self, identifier: Any) -> Any:
        return self._resolve_identifier(identifier, self._cache.scene_lookup)

    def _should_bootstrap(self, message: str) -> bool:
        if not message:
            return False
        lower = message.lower()
        return any(keyword in lower for keyword in _AUTOMATION_KEYWORDS)

    async def refresh(self, space_id: Optional[str]) -> Optional[Dict[str, Any]]:
        target = space_id or self.default_space_id
        if not target:
            return None
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(describe_space, target),
                timeout=self.bootstrap_timeout,
            )
            if isinstance(payload, dict):
                self._update_cache(target, payload)
                return payload
        except asyncio.TimeoutError:
            print(f"⚠️ Tuya describe_space timed out after {self.bootstrap_timeout}s for space {target}.")
        except Exception as exc:  # pragma: no cover - network failure
            print(f"⚠️ Could not refresh Tuya context: {exc}")
        return self._cache.payload if self._cache.payload else None

    async def ensure_context(self, space_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        target = space_id or self.default_space_id
        if not target:
            return None
        if self._is_valid(target):
            return self._cache.payload

        # If cache exists but is stale, use it immediately and refresh in the background.
        if self._cache.payload and self._cache.space_id == target:
            asyncio.create_task(self.refresh(target))
            return self._cache.payload

        return await self.refresh(target)

    async def augment_user_input(self, user_input: str) -> tuple[str, bool]:
        if not self._should_bootstrap(user_input):
            return user_input, False
        cached = await self.ensure_context(self.default_space_id)
        if not cached:
            return user_input, False
        context_text = self._format_context(cached)
        augmented = f"{context_text}\n\nUser: {user_input}"
        return augmented, True

def prewarm_tuya_caches(space_id: Optional[str]) -> None:
    """
    Pre-fetch Tuya context to reduce latency on first request.
    """
    # This function was originally in integrations.tuya.ai_tools but imported in gemini.py
    # We can just define a simple wrapper here or import the one from ai_tools if it exists.
    # Checking the imports in gemini.py:
    # from integrations.tuya.ai_tools import prewarm_tuya_caches
    # So it is already in ai_tools. We can just re-export it or let consumers import from ai_tools.
    # For compatibility with the extraction, we'll just import it.
    from integrations.tuya.ai_tools import prewarm_tuya_caches as _prewarm
    _prewarm(space_id)
