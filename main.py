from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable
from dotenv import load_dotenv
from pathlib import Path
import uvicorn
import os
from api import endpoints
from core.alexa import router as alexa_router
from core import goodweApi
from core import sqlite

# Load environment variables
load_dotenv()

sqlite.start_sqlite()

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

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend" / "public"

app = FastAPI(
    title="BotSolar API",
    description="Comprehensive API for solar generation queries and battery management",
    version="2.0.0"
)

app.include_router(endpoints.router, prefix="/api")
app.include_router(alexa_router, prefix="/api")

# Serve the generated Eleventy site at /demo
app.mount("/demo", StaticFiles(directory=FRONTEND_DIR, html=True), name="demo")

class ClientIPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        xff = request.headers.get("x-forwarded-for")
        x_real = request.headers.get("x-real-ip")
        if xff:
            ip = xff.split(",")[0].strip()
        elif x_real:
            ip = x_real.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else None

        request.state.client_ip = ip
        response = await call_next(request)
        return response

app.add_middleware(ClientIPMiddleware)


# Back-compat: expose chat endpoint at top-level /chat for legacy front-end
@app.post("/chat", response_model=endpoints.ChatResponse)
async def chat_alias(req_model: endpoints.ChatRequest, request: Request):
    client_ip = getattr(request.state, "client_ip", None)
    # opcional: injeta no modelo de requisição se ele tiver o campo
    if hasattr(req_model, "user_ip"):
        setattr(req_model, "user_ip", client_ip)
    # encaminha para o endpoint existente (ajuste assinatura se necessário)
    return await endpoints.chat_endpoint(req_model, request)

if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8001)