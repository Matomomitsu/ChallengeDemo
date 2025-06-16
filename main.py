from fastapi import FastAPI
from dotenv import load_dotenv
import uvicorn
import os
from api import endpoints

# Load environment variables
load_dotenv()

# Check if API key is loaded
api_key = os.getenv("GEMINI_API_KEY")

if api_key:
    print("✅ API key loaded successfully!")
    print(f"API key starts with: {api_key[:10]}...")
else:
    print("❌ API key not found!")
    print("Make sure you have a .env file with GEMINI_API_KEY=your_key")

app = FastAPI(
    title="BotSolar API",
    description="Comprehensive API for solar generation queries and battery management",
    version="2.0.0"
)

app.include_router(endpoints.router)

if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8001)