# GoodWe → Tuya SOC Bridge



## What it does
- Polls GoodWe telemetry via `GoodweApi` every 60 seconds (configurable) with ±5 s jitter.
- Maps SOC/status plus inverter KPIs to Tuya identifiers (`Bateria`, `status`, `Consumo_Residencial`, `Producao_Solar_Atual`, `Energia_Hoje`, `Energia_Este_Mes`, `Receita_Hoje`).
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
- TuyaLink console → Product → Online Debugging shows `Bateria` and `status` updates every ~60 s.
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
    "Bateria": {"value": 62},
    "status": {"value": "carregando"},
    "Consumo_Residencial": {"value": 412},
    "Producao_Solar_Atual": {"value": 1234},
    "Energia_Hoje": {"value": 28},
    "Energia_Este_Mes": {"value": 618},
    "Receita_Hoje": {"value": 7}
  }
}
```

If the `data` wrapper or `value` keys are omitted (e.g., sending `{"properties": {"Bateria": 62}}`), Tuya silently discards the report even though the device shows as online. Keep this structure when extending the bridge with new telemetry points.
