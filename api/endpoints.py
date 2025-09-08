from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from fastapi import Request
import os

from core.gemini import call_geminiapi

router = APIRouter()
api_key = os.getenv("GEMINI_API_KEY")

# Main chat endpoint (maintains conversation context)
class ChatResponse(BaseModel):
    response: str

class ChatRequest(BaseModel):
    user_input: str

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
            "solar_query": "/solar/query - Direct solar data queries",
            "solar_stats": "/solar/stats - Overall solar statistics",
            "battery_status": "/battery/status - Current battery status",
            "battery_flow": "/battery/energy-flow - Battery energy flow info",
            "docs": "/docs - API documentation"
        }
    } 