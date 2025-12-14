"""Bridge GoodWe SOC telemetry to TuyaLink MQTT with dual-interval heartbeat strategy."""
from __future__ import annotations

import logging
import os
import random
import re
import signal
import sys
import threading
import time
from typing import Any, Dict, Optional
from pathlib import Path
import json

from dotenv import load_dotenv

from core.goodweApi import GoodweApi
from integrations.tuya.status_mapping import DEFAULT_STATUS, STATUS_MAP, HEARTBEAT_DP_IDENTIFIER
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

# Thread-safe cache for telemetry data
_cached_telemetry: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_goodwe_status = "starting"  # Values: "ok", "error", "starting", "shutdown"
_goodwe_status_lock = threading.Lock()


def _snapshot_path() -> Path:
    """Resolve the path where we persist the latest telemetry snapshot.

    Allows override via TELEMETRY_SNAPSHOT_PATH; defaults to data/last_inverter_telemetry.json.
    """
    override = os.getenv("TELEMETRY_SNAPSHOT_PATH", "").strip()
    if override:
        return Path(override)
    return Path("data") / "last_inverter_telemetry.json"


def _persist_snapshot(snapshot: Dict[str, Any]) -> None:
    """Write a compact JSON snapshot to disk for FastAPI/ESP32 consumption.

    Best‑effort: ignore IO errors so we don't break the bridge publish loop.
    """
    try:
        path = _snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Abre diretamente o destino em modo 'w' (trunca o conteúdo anterior)
        with path.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                # Não falhar se fsync não for suportado ou der erro
                pass
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.debug("Failed to persist telemetry snapshot: %s", exc)


def _setup_logging() -> None:
    log_level = os.getenv("TUYA_SOC_LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _read_heartbeat_interval() -> int:
    """Read fast heartbeat interval (default: 10 seconds)."""
    interval_str = os.getenv("TUYA_HEARTBEAT_INTERVAL", "10").strip()
    try:
        interval = int(interval_str)
    except ValueError:
        LOGGER.warning("Invalid TUYA_HEARTBEAT_INTERVAL '%s'; defaulting to 10", interval_str)
        interval = 10
    return max(5, interval)  # Minimum 5 seconds


def _read_data_poll_interval() -> int:
    """Read GoodWe data polling interval (default: 300 seconds / 5 minutes)."""
    interval_str = os.getenv("TUYA_DATA_POLL_INTERVAL", "300").strip()
    try:
        interval = int(interval_str)
    except ValueError:
        LOGGER.warning("Invalid TUYA_DATA_POLL_INTERVAL '%s'; defaulting to 300", interval_str)
        interval = 300
    return max(60, interval)  # Minimum 60 seconds


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


def _get_goodwe_status() -> str:
    """Get the current GoodWe connection status (thread-safe)."""
    global _goodwe_status
    with _goodwe_status_lock:
        return _goodwe_status


def _set_goodwe_status(status: str) -> None:
    """Set the current GoodWe connection status (thread-safe)."""
    global _goodwe_status
    with _goodwe_status_lock:
        _goodwe_status = status


def _get_cached_telemetry() -> Dict[str, Any]:
    """Get a copy of cached telemetry (thread-safe)."""
    with _cache_lock:
        return dict(_cached_telemetry)


def _update_cached_telemetry(data: Dict[str, Any]) -> None:
    """Update cached telemetry with new data (thread-safe)."""
    with _cache_lock:
        _cached_telemetry.update(data)


def _fetch_goodwe_data(api: GoodweApi, powerstation_id: str) -> Dict[str, Any]:
    """Fetch all telemetry data from GoodWe API and return Tuya-formatted properties."""
    properties: Dict[str, Any] = {}

    # Fetch SOC data
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

    # Fetch monitor summary
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

    return properties


def _data_poll_loop(
    api: GoodweApi,
    powerstation_id: str,
    publisher,
    data_poll_interval: int,
    stop_event: threading.Event,
) -> None:
    """Slow data poll loop - fetches from GoodWe every 5 minutes (configurable)."""
    LOGGER.info("Data poll loop started (interval=%ss)", data_poll_interval)

    while not stop_event.is_set():
        try:
            properties = _fetch_goodwe_data(api, powerstation_id)

            if properties:
                # Update cache with fresh data
                _update_cached_telemetry(properties)
                _set_goodwe_status("ok")

                # Build and persist snapshot for local visualization
                now_ts = int(time.time())
                cached = _get_cached_telemetry()
                snapshot: Dict[str, Any] = {
                    "powerstation_id": powerstation_id,
                    "timestamp": now_ts,
                    "battery_soc": cached.get(TUYA_PROPERTY_IDENTIFIERS["battery_soc"]),
                    "status": cached.get(TUYA_PROPERTY_IDENTIFIERS["status"]),
                    "load_w": cached.get(TUYA_PROPERTY_IDENTIFIERS["load_w"]),
                    "pv_power_w": cached.get(TUYA_PROPERTY_IDENTIFIERS["pv_power_w"]),
                    "eday_kwh": cached.get(TUYA_PROPERTY_IDENTIFIERS["inverter_eday_kwh"]),
                    "emonth_kwh": cached.get(TUYA_PROPERTY_IDENTIFIERS["inverter_emonth_kwh"]),
                    "day_income": cached.get(TUYA_PROPERTY_IDENTIFIERS["kpi_day_income_usd"]),
                    "tuya": cached,
                }
                _persist_snapshot(snapshot)

                LOGGER.info(
                    "Fetched GoodWe data for plant %s: %s",
                    powerstation_id,
                    properties,
                )
            else:
                LOGGER.warning(
                    "No telemetry properties available from GoodWe for plant %s",
                    powerstation_id,
                )
                _set_goodwe_status("error")

        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception("Error during GoodWe data polling: %s", exc)
            _set_goodwe_status("error")

        # Wait for interval with jitter, but check stop_event frequently
        jitter = random.uniform(-MAX_JITTER_SECONDS, MAX_JITTER_SECONDS)
        sleep_duration = max(60.0, data_poll_interval + jitter)
        stop_event.wait(sleep_duration)

    LOGGER.info("Data poll loop stopped")


def _heartbeat_loop(
    publisher,
    heartbeat_interval: int,
    stop_event: threading.Event,
) -> None:
    """Fast heartbeat loop - sends cached telemetry + status every 10 seconds (configurable)."""
    LOGGER.info("Heartbeat loop started (interval=%ss)", heartbeat_interval)

    while not stop_event.is_set():
        try:
            # Build heartbeat payload with cached telemetry
            cached = _get_cached_telemetry()
            status = _get_goodwe_status()
            timestamp = int(time.time())

            # Create payload with heartbeat DP + all cached telemetry
            payload = dict(cached)
            payload[HEARTBEAT_DP_IDENTIFIER] = f"{status}_{timestamp}"

            # Publish heartbeat with cached data
            try:
                publisher.report(payload)
                LOGGER.debug(
                    "Heartbeat published: goodwe_ok=%s, cached_props=%d",
                    payload[HEARTBEAT_DP_IDENTIFIER],
                    len(cached),
                )
            except Exception as pub_exc:  # pylint: disable=broad-except
                LOGGER.warning("Heartbeat publish failed: %s", pub_exc)

        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception("Error during heartbeat: %s", exc)

        # Wait for interval, but check stop_event
        stop_event.wait(heartbeat_interval)

    LOGGER.info("Heartbeat loop stopped")


def main() -> None:
    _setup_logging()

    powerstation_id = os.getenv("GOODWE_POWERSTATION_ID", "").strip()
    if not powerstation_id:
        LOGGER.error("GOODWE_POWERSTATION_ID is required")
        sys.exit(1)

    heartbeat_interval = _read_heartbeat_interval()
    data_poll_interval = _read_data_poll_interval()

    LOGGER.info(
        "Starting GoodWe → Tuya SOC bridge (powerstation_id=%s, heartbeat=%ss, data_poll=%ss)",
        powerstation_id,
        heartbeat_interval,
        data_poll_interval,
    )

    publisher = build_publisher_from_env()
    if not publisher.dry_run:
        publisher.connect()

    api = GoodweApi()

    # Event to signal threads to stop
    stop_event = threading.Event()

    # Start data poll thread
    data_thread = threading.Thread(
        target=_data_poll_loop,
        args=(api, powerstation_id, publisher, data_poll_interval, stop_event),
        daemon=True,
        name="GoodWeDataPoll",
    )
    data_thread.start()

    # Start heartbeat thread
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(publisher, heartbeat_interval, stop_event),
        daemon=True,
        name="TuyaHeartbeat",
    )
    heartbeat_thread.start()

    # Signal handler for graceful shutdown
    def signal_handler(signum, frame):
        LOGGER.info("Received signal %s; initiating graceful shutdown...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Wait for stop signal
        while not stop_event.is_set():
            stop_event.wait(1)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted; shutting down GoodWe → Tuya SOC bridge.")
        stop_event.set()
    finally:
        # Send graceful shutdown notification
        try:
            LOGGER.info("Sending shutdown notification to Tuya...")
            shutdown_payload = _get_cached_telemetry()
            shutdown_payload[HEARTBEAT_DP_IDENTIFIER] = "shutdown"
            publisher.report(shutdown_payload)
            LOGGER.info("Shutdown notification sent successfully")
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Failed to send shutdown notification: %s", exc)

        # Wait for threads to finish (with timeout)
        data_thread.join(timeout=5)
        heartbeat_thread.join(timeout=2)

        try:
            publisher.close()
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.debug("Error during publisher shutdown: %s", exc)


if __name__ == "__main__":
    main()
