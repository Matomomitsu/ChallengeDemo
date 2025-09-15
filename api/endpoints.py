from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from fastapi import Request
import os

from core.gemini import call_geminiapi
from core.goodweApi import GoodweApi
import os

router = APIRouter()
api_key = os.getenv("GEMINI_API_KEY")

# Main chat endpoint (maintains conversation context)
class ChatResponse(BaseModel):
    response: str

class ChatRequest(BaseModel):
    user_input: str

# (removed) CSV-based solar query request schema

goodwe_api = GoodweApi()
DEFAULT_STATION_NAME = "Bauer"
DEFAULT_STATION_ID = "6ef62eb2-7959-4c49-ad0a-0ce75565023a"

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
        response = await call_geminiapi(request.user_input)
        return ChatResponse(response=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


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
            "docs": "/docs - API documentation"
        }
    } 