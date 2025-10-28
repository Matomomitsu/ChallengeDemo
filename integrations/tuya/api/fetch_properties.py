"""CLI helper to fetch Tuya device shadow properties."""
from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

from .client import DEFAULT_TUYA_API_BASE_URL, TuyaApiClient, TuyaApiError


def _build_client_from_env() -> TuyaApiClient:
    client_id = os.getenv("TUYA_CLIENT_ID", "").strip()
    client_secret = os.getenv("TUYA_CLIENT_SECRET", "").strip()
    base_url = os.getenv("TUYA_API_BASE_URL", DEFAULT_TUYA_API_BASE_URL).strip() or DEFAULT_TUYA_API_BASE_URL

    if not client_id or not client_secret:
        missing = [name for name, value in [("TUYA_CLIENT_ID", client_id), ("TUYA_CLIENT_SECRET", client_secret)] if not value]
        raise SystemExit(f"Missing required Tuya credentials: {', '.join(missing)}")

    return TuyaApiClient(client_id=client_id, client_secret=client_secret, base_url=base_url)


def main() -> None:
    load_dotenv()

    device_id = os.getenv("TUYA_DEVICE_ID", "").strip()
    if not device_id:
        raise SystemExit("TUYA_DEVICE_ID is required to query device properties")

    project_code = os.getenv("TUYA_PROJECT_CODE", "").strip()
    if not project_code:
        print("Warning: TUYA_PROJECT_CODE is not set; ensure your cloud project matches the device.", file=sys.stderr)

    client = _build_client_from_env()
    try:
        result = client.get_device_shadow_properties(device_id)
    except TuyaApiError as exc:
        raise SystemExit(f"Tuya API request failed: {exc}") from exc

    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
