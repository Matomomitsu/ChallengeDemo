"""Bridge GoodWe SOC telemetry to TuyaLink MQTT."""
from __future__ import annotations

import logging
import os
import random
import sys
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from core.goodweApi import GoodweApi
from integrations.tuya.status_mapping import DEFAULT_STATUS, STATUS_MAP
from integrations.tuya.tuyalink_publisher import build_publisher_from_env

load_dotenv()

LOGGER = logging.getLogger(__name__)
MAX_JITTER_SECONDS = 5


def _setup_logging() -> None:
    log_level = os.getenv("TUYA_SOC_LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _read_poll_interval() -> int:
    interval_str = os.getenv("TUYA_SOC_POLL_INTERVAL", "60").strip()
    try:
        interval = int(interval_str)
    except ValueError:
        LOGGER.warning("Invalid TUYA_SOC_POLL_INTERVAL '%s'; defaulting to 60", interval_str)
        interval = 60
    return max(10, interval)


def _extract_first_soc_entry(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    soc_list = payload.get("soc") if isinstance(payload, dict) else None
    if isinstance(soc_list, list) and soc_list:
        first = soc_list[0]
        if isinstance(first, dict):
            return first
    return None


def _coerce_soc(raw_value: Any) -> Optional[int]:
    if raw_value is None:
        return None
    try:
        soc_float = float(raw_value)
    except (TypeError, ValueError):
        return None

    soc_int = int(round(soc_float))
    if soc_int < 0 or soc_int > 100:
        LOGGER.warning("SOC value %s outside 0-100 range; clamping", soc_int)
        soc_int = max(0, min(100, soc_int))
    return soc_int


def _map_status(raw_status: Any) -> str:
    try:
        status_code = int(raw_status)
    except (TypeError, ValueError):
        status_code = None

    if status_code in STATUS_MAP:
        return STATUS_MAP[status_code]

    LOGGER.warning("Unknown GoodWe status code '%s'; defaulting to %s", raw_status, DEFAULT_STATUS)
    return DEFAULT_STATUS


def _sleep_with_jitter(base_seconds: int) -> None:
    jitter = random.uniform(-MAX_JITTER_SECONDS, MAX_JITTER_SECONDS)
    sleep_duration = max(5.0, base_seconds + jitter)
    time.sleep(sleep_duration)


def main() -> None:
    _setup_logging()

    powerstation_id = os.getenv("GOODWE_POWERSTATION_ID", "").strip()
    if not powerstation_id:
        LOGGER.error("GOODWE_POWERSTATION_ID is required")
        sys.exit(1)

    poll_interval = _read_poll_interval()

    LOGGER.info(
        "Starting GoodWe → Tuya SOC bridge (powerstation_id=%s, interval=%ss)",
        powerstation_id,
        poll_interval,
    )

    publisher = build_publisher_from_env()
    if not publisher.dry_run:
        publisher.connect()

    api = GoodweApi()

    try:
        while True:
            try:
                soc_payload = api.GetSoc(powerstation_id)
                entry = _extract_first_soc_entry(soc_payload)
                if not entry:
                    LOGGER.warning("No SOC data returned for plant %s", powerstation_id)
                else:
                    soc_value = _coerce_soc(entry.get("power"))
                    status_value = _map_status(entry.get("status"))

                    if soc_value is None:
                        LOGGER.warning("Missing or invalid SOC 'power' value in response: %s", entry)
                    else:
                        properties = {
                            "battery_soc": soc_value,
                            "status": status_value,
                        }
                        publisher.report(properties)
                        LOGGER.info(
                            "Published SOC for plant %s: battery_soc=%s status=%s",
                            powerstation_id,
                            soc_value,
                            status_value,
                        )
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("Error during SOC polling/publish: %s", exc)

            _sleep_with_jitter(poll_interval)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted; shutting down GoodWe → Tuya SOC bridge.")
    finally:
        try:
            publisher.close()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.debug("Error during publisher shutdown: %s", exc)


if __name__ == "__main__":
    main()
