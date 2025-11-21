# BotSolar — GoodWe + Tuya Assistant

Unified platform for GoodWe SEMS monitoring and Tuya smart-home automation with a FastAPI backend, English-first system prompt, and a CLI for local ops.

## What It Does
- **GoodWe monitoring:** battery SOC, EV charging mode, alarms by date range, translated alarm details.
- **Tuya automation:** discover devices/scenes, inspect datapoints, propose or build automations, create/update/delete/enable scenes with confirmation gates.
- **Interfaces:** FastAPI REST API with OpenAPI docs, interactive CLI, optional frontend demo (`frontend/`).

## Prerequisites
- Python 3.9+
- Google Gemini API key
- GoodWe SEMS credentials
- (Optional) Tuya Cloud keys for automation features

## Setup
```bash
pip install -r requirements.txt
```

Create a `.env` file at the repo root:
```
GEMINI_API_KEY=your_gemini_key
GOODWE_ACCOUNT=your_goodwe_username
GOODWE_PASSWORD=your_goodwe_password
# Default plant (optional; picked by name if ID is omitted)
DEFAULT_POWERSTATION_NAME=YourPlantName
# Optional explicit plant ID
# DEFAULT_POWERSTATION_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Tuya (optional for automation features)
TUYA_CLIENT_ID=...
TUYA_CLIENT_SECRET=...
TUYA_SPACE_ID=...
```

## Running

**API server:**
```bash
python main.py
```
- Server: `http://localhost:8001`
- Docs: `http://localhost:8001/docs`
- Demo (prebuilt frontend): `http://localhost:8001/demo`

**CLI chat:**
```bash
python cli.py
```

**Tuya Automation CLI:** see `docs/tuya_automation.md` and run `python -m integrations.tuya.cli`.

### Frontend (optional demo)
```bash
cd frontend
npm install      # first run or after dependency updates
npm run build    # outputs static site to frontend/public
```
FastAPI serves `frontend/public` directly; rebuild before restarting the API when you tweak the UI.

## API Endpoints
- `POST /chat` – Natural language interface (alias at `/api/chat`)
- `GET /battery/status` – GoodWe battery SOC for the default plant
- `GET /plants` – List plants for selection
- `GET /inverter` – Latest inverter telemetry snapshot
- `GET /health` – Health check
- `GET /` – API overview

## Project Layout
```
├── main.py                 # FastAPI application entrypoint
├── cli.py                  # Interactive CLI
├── core/
│   ├── gemini.py           # Gemini orchestrator (tool calls, retries, caching)
│   └── goodweApi.py        # GoodWe SEMS client (token, plants, SOC, alarms)
├── api/
│   └── endpoints.py        # FastAPI routes
├── integrations/tuya       # Tuya client, heuristics, AI-facing helpers
├── system_prompt.txt       # English system prompt for Gemini
└── requirements.txt
```
