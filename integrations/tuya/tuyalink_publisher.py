"""TuyaLink MQTT publisher for telemetry reporting."""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import ssl
import threading
import time
import uuid
from typing import Dict

from dotenv import load_dotenv
import paho.mqtt.client as mqtt

load_dotenv()

LOGGER = logging.getLogger(__name__)


class TuyaLinkPublisher:
    """Handles authenticated, TLS-secured publishes to TuyaLink."""

    def __init__(
        self,
        device_id: str,
        device_secret: str,
        host: str,
        port: int = 8883,
        *,
        dry_run: bool = False,
        keepalive: int = 120,
        min_backoff: int = 5,
        max_backoff: int = 60,
    ) -> None:
        self.device_id = device_id
        self.device_secret = device_secret
        self.host = host
        self.port = port
        self.dry_run = dry_run
        self.keepalive = keepalive
        self._client: mqtt.Client | None = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._min_backoff = max(1, min_backoff)
        self._max_backoff = max(self._min_backoff, max_backoff)
        self._current_backoff = self._min_backoff
        self._loop_running = False

        if not self.dry_run:
            self._init_client()

    def _init_client(self) -> None:
        client_id = f"tuyalink_{self.device_id}"
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self._client.tls_set(tls_version=ssl.PROTOCOL_TLSv1_2)
        self._client.tls_insecure_set(False)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

    def _build_credentials(self) -> tuple[str, str]:
        timestamp = str(int(time.time()))
        username = (
            f"{self.device_id}|signMethod=hmacSha256,timestamp={timestamp},secureMode=1,accessType=1"
        )
        sign_payload = (
            f"deviceId={self.device_id},timestamp={timestamp},secureMode=1,accessType=1"
        )
        signature = hmac.new(
            self.device_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return username, signature

    def connect(self) -> None:
        if self.dry_run:
            LOGGER.info("TuyaLink publisher running in dry-run mode; MQTT connection skipped.")
            return

        assert self._client is not None  # nosec: defensive assertion

        with self._lock:
            if self._connected.is_set():
                return

            backoff = self._current_backoff
            while not self._connected.is_set():
                username, password = self._build_credentials()
                self._client.username_pw_set(username=username, password=password)
                try:
                    LOGGER.debug(
                        "Connecting to TuyaLink MQTT broker %s:%s (backoff=%ss)",
                        self.host,
                        self.port,
                        backoff,
                    )
                    self._client.connect(self.host, self.port, keepalive=self.keepalive)
                    self._client.loop_start()
                    self._loop_running = True
                    if self._connected.wait(timeout=15):
                        self._current_backoff = self._min_backoff
                        return
                    raise TimeoutError("Timed out waiting for TuyaLink MQTT connection")
                except Exception as exc:  # pylint: disable=broad-except
                    LOGGER.warning("TuyaLink MQTT connect failed: %s", exc)
                    self._disconnect_client()
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self._max_backoff)
                    self._current_backoff = backoff

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc):  # type: ignore[override]
        if rc == 0:
            LOGGER.info("Connected to TuyaLink MQTT broker.")
            self._connected.set()
        else:
            LOGGER.warning("TuyaLink MQTT connection refused (rc=%s)", rc)

    def _on_disconnect(self, client: mqtt.Client, userdata, rc):  # type: ignore[override]
        self._connected.clear()
        if rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("TuyaLink MQTT disconnected unexpectedly (rc=%s)", rc)
        self._stop_loop()

    def _on_publish(self, client: mqtt.Client, userdata, mid):  # type: ignore[override]
        LOGGER.debug("TuyaLink publish completed (mid=%s)", mid)

    def _stop_loop(self) -> None:
        if self._client and self._loop_running:
            self._client.loop_stop()
            self._loop_running = False

    def _disconnect_client(self) -> None:
        if self._client:
            try:
                self._client.disconnect()
            except Exception:  # pylint: disable=broad-except
                pass
        self._stop_loop()
        self._connected.clear()

    def report(self, properties: Dict[str, int | str]) -> None:
        timestamp_ms = int(time.time() * 1000)
        payload = {
            "msgId": uuid.uuid4().hex,
            "time": timestamp_ms,
            "data": {
                key: {"value": value}
                for key, value in properties.items()
            },
        }
        LOGGER.debug("TuyaLink publish payload: %s", payload)
        if self.dry_run:
            print(json.dumps(payload))  # noqa: T201 - intentional user-facing output
            LOGGER.info("Dry-run Tuya publish: %s", payload)
            return

        assert self._client is not None  # nosec: defensive assertion

        with self._lock:
            if not self._connected.is_set():
                self.connect()

            if not self._connected.is_set():
                raise ConnectionError("Unable to establish TuyaLink MQTT connection")

            topic = f"tylink/{self.device_id}/thing/property/report"
            message = json.dumps(payload)
            result = self._client.publish(topic, message, qos=1)
            result.wait_for_publish(timeout=10)
            if result.rc != mqtt.MQTT_ERR_SUCCESS or not result.is_published():
                raise ConnectionError(f"Failed to publish to TuyaLink (rc={result.rc})")

    def close(self) -> None:
        if self.dry_run:
            return
        self._disconnect_client()


def build_publisher_from_env() -> TuyaLinkPublisher:
    """Factory helper that reads configuration from environment variables."""
    device_id = os.getenv("TUYA_DEVICE_ID", "").strip()
    device_secret = os.getenv("TUYA_DEVICE_SECRET", "").strip()
    host = os.getenv("TUYA_MQTT_HOST", "").strip()
    port_str = os.getenv("TUYA_MQTT_PORT", "8883").strip()

    dry_run = not (device_id and device_secret and host)

    try:
        port = int(port_str)
    except ValueError:
        LOGGER.warning("Invalid TUYA_MQTT_PORT value '%s'; defaulting to 8883", port_str)
        port = 8883

    if dry_run:
        return TuyaLinkPublisher(
            device_id=device_id or "dry-run-device",
            device_secret=device_secret or "dry-run-secret",
            host=host or "dry-run-host",
            dry_run=True,
        )

    return TuyaLinkPublisher(
        device_id=device_id,
        device_secret=device_secret,
        host=host,
        port=port,
    )
