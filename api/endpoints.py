from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import os

from core.gemini import call_geminiapi
import core.solar_tools as solar_tools
import core.battery as battery

router = APIRouter()
api_key = os.getenv("GEMINI_API_KEY")

# Request/Response Models
class ChatRequest(BaseModel):
    user_input: str

class ChatResponse(BaseModel):
    response: str

class SolarQueryRequest(BaseModel):
    start_date: str
    end_date: Optional[str] = None

class SolarStatsResponse(BaseModel):
    total_kwh: float
    average_daily_kwh: float
    max_daily_kwh: float
    min_daily_kwh: float
    total_days: int

class BatteryDestinationRequest(BaseModel):
    destinations: list[str]

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

# Legacy endpoint for backward compatibility
@router.post("/command")
async def process_data(data: ChatRequest):
    """
    📋 Legacy Command Processor
    
    Backward compatibility endpoint that redirects to the new chat interface.
    Maintained for existing integrations.
    """
    return await chat_endpoint(data)

# Direct solar generation query endpoint
@router.post("/solar/query")
async def solar_query(request: SolarQueryRequest):
    """
    ☀️ Solar Generation Data Query
    
    Direct access to solar generation data with date range filtering.
    Perfect for programmatic access to historical solar data.
    
    - Supports single date or date range queries
    - Returns energy generation in kWh
    - Includes period summary
    """
    try:
        result = solar_tools.query_generation(request.start_date, request.end_date)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error querying solar data: {str(e)}")

# Solar statistics endpoint
@router.get("/solar/stats", response_model=SolarStatsResponse)
async def solar_stats():
    """
    📊 Solar System Performance Statistics
    
    Comprehensive analytics for your solar installation including:
    - Total energy generated (kWh)
    - Daily averages, maximum, and minimum values
    - Complete performance overview
    """
    try:
        stats = solar_tools.get_solar_stats()
        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])
        return SolarStatsResponse(**stats)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting solar stats: {str(e)}")

# Battery status endpoint
@router.get("/battery/status")
async def battery_status():
    """
    🔋 Battery Management System Status
    
    Real-time battery information including:
    - Current charge level
    - Charging/discharging status
    - Energy flow direction
    
    ⚠️ Note: Currently using placeholder data. Will be integrated with real solar inverter data.
    """
    try:
        status = battery.get_batery_usage()
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting battery status: {str(e)}")

# Battery energy flow endpoint
@router.get("/battery/energy-flow")
async def battery_energy_flow():
    """
    ⚡ Battery Energy Flow Management
    
    Monitor where your battery energy is being directed:
    - Active energy destinations
    - Power distribution overview
    - Load management insights
    
    ⚠️ Note: Placeholder implementation - real inverter integration coming soon.
    """
    try:
        flow = battery.check_battery_energy_flow()
        return flow
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking battery energy flow: {str(e)}")

# Add battery destinations endpoint
@router.post("/battery/add-destinations")
async def add_battery_destinations(request: BatteryDestinationRequest):
    """
    ➕ Add Battery Energy Destinations
    
    Dynamically add new destinations for battery energy flow:
    - Home appliances
    - Electric vehicle charging
    - Grid feed-in
    
    ⚠️ Note: Demonstration functionality - real device control pending inverter integration.
    """
    try:
        result = battery.add_destination_to_battery_flow(request.destinations)
        return {"message": "Destinations added successfully", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding battery destinations: {str(e)}")

# Remove battery destinations endpoint
@router.post("/battery/remove-destinations")
async def remove_battery_destinations(request: BatteryDestinationRequest):
    """
    ➖ Remove Battery Energy Destinations
    
    Remove destinations from active battery energy flow:
    - Stop charging specific devices
    - Redirect energy to priority loads
    - Optimize energy distribution
    
    ⚠️ Note: Demonstration functionality - real device control pending inverter integration.
    """
    try:
        result = battery.remove_destination_from_battery_flow(request.destinations)
        return {"message": "Destinations removed successfully", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing battery destinations: {str(e)}")

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