# GoodWe → Tuya SOC Bridge



## What it does
- Polls GoodWe telemetry via `GoodweApi` every 60 seconds (configurable) with ±5 s jitter.
- Maps SOC/status plus inverter KPIs (`load_w`, `pv_power_w`, `inverter_eday_kwh`, `inverter_emonth_kwh`, `kpi_day_income_usd`).
- Publishes the mapped telemetry to TuyaLink MQTT over TLS using device credentials, or prints the payload when Tuya credentials are absent (dry-run).

## Configuration
1. Copy `.env.example` to `.env` (or merge with your current `.env`).
2. Fill in:
   - `TUYA_DEVICE_ID` and `TUYA_DEVICE_SECRET` from the TuyaLink device page.
   - `TUYA_MQTT_HOST`/`TUYA_MQTT_PORT` for the Tuya data center (default `m1.tuyacn.com:8883`).
   - `GOODWE_ACCOUNT`, `GOODWE_PASSWORD`, and `GOODWE_POWERSTATION_ID` (already in use by the project).
   - Optional tuning: `TUYA_SOC_POLL_INTERVAL` (seconds, minimum 10) and `TUYA_SOC_LOG_LEVEL`.
3. Install dependencies: `pip install -r requirements.txt` (ensures `paho-mqtt` and `python-dotenv`).

## Running the bridge
```shell
python -m integrations.tuya.bridge_soc
```

The bridge logs one INFO line per publish. In dry-run mode (missing Tuya env vars) it prints the JSON payload instead of connecting to TuyaLink.

## Verification checklist
- TuyaLink console → Product → Online Debugging shows `battery_soc` and `status` updates every ~60 s.
- Device status flips from offline to online once MQTT connects.
- Smart Life / Tuya app (optional) displays live SOC telemetry.
- Logs confirm one publish per polling interval and warn if SOC/status values look anomalous.

## Tuya payload format gotcha
TuyaLink expects property reports to wrap each DP inside a `data` object with individual `value` entries. The bridge now publishes:

```json
{
  "msgId": "<uuid>",
  "time": 1695391039412,
  "data": {
    "battery_soc": {"value": 62},
    "status": {"value": "charging"},
    "load_w": {"value": 412},
    "pv_power_w": {"value": 1234},
    "inverter_eday_kwh": {"value": 28},
    "inverter_emonth_kwh": {"value": 618},
    "kpi_day_income_usd": {"value": 7}
  }
}
```

If the `data` wrapper or `value` keys are omitted (e.g., sending `{"properties": {"battery_soc": 62}}`), Tuya silently discards the report even though the device shows as online. Keep this structure when extending the bridge with new telemetry points.


