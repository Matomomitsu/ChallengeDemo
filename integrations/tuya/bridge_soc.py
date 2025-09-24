"""Bridge GoodWe SOC telemetry to TuyaLink MQTT."""
from __future__ import annotations

import logging
import os
import random
import re
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

# Mapping between logical telemetry keys and Tuya DP identifiers configured in the cloud
TUYA_PROPERTY_IDENTIFIERS = {
    "battery_soc": "Bateria",
    "status": "status",
    "load_w": "Consumo_Residencial",
    "pv_power_w": "Producao_Solar_Atual",
    "inverter_eday_kwh": "Energia_Hoje",
    "inverter_emonth_kwh": "Energia_Este_Mes",
    "kpi_day_income_usd": "Receita_Hoje",
}


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


def _coerce_power(value: Any) -> Optional[int]:
    """Convert GoodWe power strings (e.g. '236.5(W)') or numbers to int watts."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))

    text = str(value).strip().lower()
    if not text:
        return None

    multiplier = 1
    if "kw" in text and "mw" not in text:
        multiplier = 1000

    match = re.search(r"[-+]?\d*\.?\d+", text.replace(",", "."))
    if not match:
        return None

    try:
        number = float(match.group()) * multiplier
    except ValueError:
        return None

    return int(round(number))


def _coerce_integer_metric(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))

    text = str(value).strip().replace(",", ".")
    if not text:
        return None

    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return None

    try:
        number = float(match.group())
    except ValueError:
        return None

    return int(round(number))


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
                properties: Dict[str, Any] = {}

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
                        properties[TUYA_PROPERTY_IDENTIFIERS["battery_soc"]] = soc_value
                        properties[TUYA_PROPERTY_IDENTIFIERS["status"]] = status_value

                summary = api.GetMonitorSummaryByPowerstationId(powerstation_id)
                summary_data = (
                    summary.get("data")
                    if isinstance(summary, dict) and not summary.get("hasError")
                    else {}
                )

                if summary_data:
                    load_w = _coerce_power(summary_data.get("load"))
                    pv_w = _coerce_power(summary_data.get("pv"))
                    eday = _coerce_integer_metric(summary_data.get("eday"))
                    emonth = _coerce_integer_metric(summary_data.get("emonth"))
                    day_income = _coerce_integer_metric(summary_data.get("day_income"))

                    if load_w is not None:
                        properties[TUYA_PROPERTY_IDENTIFIERS["load_w"]] = load_w
                    if pv_w is not None:
                        properties[TUYA_PROPERTY_IDENTIFIERS["pv_power_w"]] = pv_w
                    if eday is not None:
                        properties[TUYA_PROPERTY_IDENTIFIERS["inverter_eday_kwh"]] = eday
                    if emonth is not None:
                        properties[TUYA_PROPERTY_IDENTIFIERS["inverter_emonth_kwh"]] = emonth
                    if day_income is not None:
                        properties[TUYA_PROPERTY_IDENTIFIERS["kpi_day_income_usd"]] = day_income
                elif isinstance(summary, dict) and summary.get("hasError"):
                    LOGGER.warning(
                        "Failed to fetch GoodWe monitor summary for plant %s: %s",
                        powerstation_id,
                        summary.get("msg") or summary.get("code") or summary,
                    )

                if properties:
                    publisher.report(properties)
                    LOGGER.info(
                        "Published Tuya telemetry for plant %s: %s",
                        powerstation_id,
                        properties,
                    )
                else:
                    LOGGER.warning(
                        "No telemetry properties available to publish for plant %s",
                        powerstation_id,
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
