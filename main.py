from fastapi import FastAPI
from dotenv import load_dotenv
import uvicorn
import os
from api import endpoints
from core.alexa import router as alexa_router

# Load environment variables
load_dotenv()

# Check if API key is loaded
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    print("✅ API key loaded successfully!")
else:
    print("❌ API key not found!")
    print("Make sure you have a .env file with GEMINI_API_KEY=your_key")

goodwe_account = os.getenv("GOODWE_ACCOUNT")
goodwe_password = os.getenv("GOODWE_PASSWORD")

if goodwe_account:
    print("✅ GOODWE_ACCOUNT loaded successfully!")
else:
    print("❌ GOODWE_ACCOUNT not found!")
    print("Make sure you have a .env file with GOODWE_ACCOUNT=your_account")

if goodwe_password:
    print("✅ GOODWE_PASSWORD loaded successfully!")
else:
    print("❌ GOODWE_PASSWORD not found!")
    print("Make sure you have a .env file with GOODWE_PASSWORD=your_password")

app = FastAPI(
    title="BotSolar API",
    description="Comprehensive API for solar generation queries and battery management",
    version="2.0.0"
)

app.include_router(endpoints.router)
app.include_router(alexa_router, prefix="/alexa")

if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
