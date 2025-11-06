from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from fastapi import Request
import os
import json
from pathlib import Path

from core.gemini import call_geminiapi
from core.goodweApi import GoodweApi
import os

router = APIRouter()
api_key = os.getenv("GEMINI_API_KEY")

# Main chat endpoint (maintains conversation context)
class FunctionPreview(BaseModel):
    name: str
    args: Dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    response: str
    functions_preview: Optional[List[FunctionPreview]] = None
    fallback_to_default: Optional[bool] = False
    used_powerstation_id: Optional[str] = None


class ChatRequest(BaseModel):
    user_input: str
    plant_id: Optional[str] = None


class PlantInfo(BaseModel):
    id: str
    name: str


class PlantListResponse(BaseModel):
    plants: List[PlantInfo]

# (removed) CSV-based solar query request schema

goodwe_api = GoodweApi()
DEFAULT_STATION_NAME = os.getenv("DEFAULT_STATION_NAME")
DEFAULT_STATION_ID = os.getenv("DEFAULT_STATION_ID")

# Main chat endpoint (maintains conversation context)
@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    🤖 AI-Powered Chat Interface
    
    Main conversational endpoint that handles natural language queries for:
    - Solar generation data analysis
    - Battery management commands
    - Historical data requests
    - Real-time energy monitoring
    
    Example: "How much solar energy did I generate yesterday?"
    """
    try:
        result = await call_geminiapi(request.user_input, powerstation_id=request.plant_id)
        return ChatResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


@router.get("/plants", response_model=PlantListResponse)
async def list_plants():
    """Return available plants/power stations for selection in the UI."""
    try:
        payload = goodwe_api.ListPlants() or {}
        raw_list = payload.get("plants", []) if isinstance(payload, dict) else []
        plants = [
            PlantInfo(id=item.get("powerstation_id"), name=item.get("stationname") or "")
            for item in raw_list
            if item.get("powerstation_id") and (item.get("stationname") or "").strip()
        ]
        if not plants:
            raise HTTPException(status_code=404, detail="No plants available for the configured account")
        return PlantListResponse(plants=plants)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching plant list: {str(e)}")


@router.get("/EvCharger/ChargingMode")
async def get_ev_charger_charge_mode():
    """
    🚗 EV Charger Status

    Returns the current status of the connected EV charger, including:
    - Charging state (charging, idle, error)
    - Current power draw (kW)
    - Total energy delivered (kWh)
    - Estimated time to full charge
    """
    try:
        # Placeholder implementation - replace with actual EV charger API calls
        goodwe_api = GoodweApi()
        ev_status = goodwe_api.GetEvChargerChargingMode(None)
        return ev_status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching EV charger status: {str(e)}")

# Removed CSV-based solar endpoints: /solar/query and /solar/stats

# Battery status endpoint (GoodWe-backed)
@router.get("/battery/status")
async def battery_status():
    """
    🔋 Battery Status (GoodWe)

    Returns battery status information from the GoodWe SEMS portal. Automatically
    selects the first available plant.
    """
    try:
        powerstation_id = DEFAULT_STATION_ID
        if not powerstation_id:
            plants = goodwe_api.ListPlants() or {}
            plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
            if not plant_list:
                raise HTTPException(status_code=404, detail="No plants available for the configured account")
            # Prefer DEFAULT_STATION_NAME when present
            preferred = next((p for p in plant_list if (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower()), None)
            powerstation_id = (preferred or plant_list[0]).get("powerstation_id")
        soc = goodwe_api.GetSoc(powerstation_id)
        if soc is None:
            raise HTTPException(status_code=502, detail="Failed to fetch battery status from GoodWe")
        return {"powerstation_id": powerstation_id, **(soc or {})}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting battery status: {str(e)}")

# Removed legacy placeholder endpoints: /battery/energy-flow, add/remove-destinations
@router.post("/google/webhook")
async def google_webhook(request: Request):
    """
    Webhook para integração com Google Assistant/Dialogflow.
    """
    try:
        body = await request.json()
        user_input = body.get("queryResult", {}).get("queryText", "")
        response_text = await call_geminiapi(user_input)
        return {
            "fulfillmentMessages": [
                {
                    "text": {
                        "text": [response_text]
                    }
                }
            ],
            "payload": {
                "google": {
                    "expectUserResponse": True,
                    "richResponse": {
                        "items": [
                            {
                                "simpleResponse": {
                                    "textToSpeech": response_text
                                }
                            }
                        ]
                    }
                }
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao processar o webhook: {str(e)}")

# Health check endpoint
@router.get("/health")
async def health_check():
    """
    🩺 System Health Check
    
    Verify system status and configuration:
    - API connectivity
    - Gemini AI integration status
    - System version information
    """
    return {
        "status": "healthy",
        "api_key_configured": bool(api_key),
        "version": "2.0.0"
    }

# Root endpoint with API information
@router.get("/")
async def root():
    """
    🏠 BotSolar API Overview
    
    Welcome to the BotSolar API - Your comprehensive solar and battery management solution.
    
    Key Features:
    - AI-powered natural language interface
    - Historical solar data analysis
    - Real-time battery management
    - RESTful API with full documentation
    """
    return {
        "message": "BotSolar API - Solar Generation and Battery Management",
        "version": "2.0.0",
        "features": {
            "ai_chat": "Natural language solar and battery queries",
            "solar_analytics": "Historical data analysis and statistics",
            "battery_management": "Real-time battery monitoring and control",
            "api_documentation": "Complete OpenAPI/Swagger documentation"
        },
        "endpoints": {
            "chat": "/chat - Main conversational interface",
            "battery_status": "/battery/status - Current battery status (GoodWe)",
            "inverter_snapshot": "/inverter - Latest telemetry snapshot for ESP32 display",
            "docs": "/docs - API documentation"
        }
    }


# ---------- ESP32 display support ----------

class InverterSnapshot(BaseModel):
    powerstation_id: Optional[str] = None
    timestamp: int
    battery_soc: Optional[int] = None
    status: Optional[str] = None
    load_w: Optional[int] = None
    pv_power_w: Optional[int] = None
    eday_kwh: Optional[int] = None
    emonth_kwh: Optional[int] = None
    day_income: Optional[int] = None
    tuya: Optional[Dict[str, Any]] = None


def _snapshot_path() -> Path:
    override = os.getenv("TELEMETRY_SNAPSHOT_PATH", "").strip()
    if override:
        return Path(override)
    return Path("data") / "last_inverter_telemetry.json"


@router.get("/inverter", response_model=InverterSnapshot)
async def get_inverter_snapshot():
    """
    Latest inverter telemetry snapshot persisted by the Tuya SOC bridge.

    Run `python -m integrations.tuya.bridge_soc` to refresh this file periodically.
    """
    path = _snapshot_path()
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="Telemetry snapshot not available yet. Start the Tuya SOC bridge to generate it.",
        )
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Validate minimal required field
        ts = data.get("timestamp")
        if not isinstance(ts, int):
            raise ValueError("Missing/invalid timestamp in snapshot")
        return data
    except Exception as exc:  # pylint: disable=broad-except
        raise HTTPException(status_code=500, detail=f"Failed to read snapshot: {exc}")
