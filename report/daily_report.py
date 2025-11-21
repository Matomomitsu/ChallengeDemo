import os
import asyncio
from dotenv import load_dotenv
import pymongo
from datetime import datetime, timezone
from report.scene_suggestion_gemini import call_geminiapi

load_dotenv(".env")

HOST = os.getenv("MONGO_URI")
SPACE_ID = os.getenv("TUYA_SPACE_ID", "default_space")
SLEEP_SECONDS = 24 * 3600  # 24 horas

MESSAGE = (
    "Gere uma sugestão de cena como um único objeto JSON pronto para POST /v2.0/cloud/scene/rule. "
    "Nomeie a cena como: \"Sugestão automática - <breve motivo>\". "
    "Baseie-se nos dispositivos da última amostra quando possível e prefira o tipo \"automation\". "
    "Se houver dados insuficientes, aplique valores padrão sensatos. Responda apenas com o JSON final."
)

def get_db_collection():
    client = pymongo.MongoClient(HOST)
    db = client["fiap_challenge"]
    return db["scenes_sugestions"]

async def run_once_and_store(collection):
    """Chama o agente e armazena o resultado no MongoDB."""
    try:
        result = await call_geminiapi(MESSAGE)
        doc = {
            "created_at": datetime.now(timezone.utc),
            "space_id": SPACE_ID,
            "result": result
        }
        res = collection.insert_one(doc)
        print(f"Suggestion stored with _id: {res.inserted_id}")
    except Exception as e:
        print(f"Error running agent: {e}")

async def daily_loop():
    collection = get_db_collection()
    while True:
        await run_once_and_store(collection)
        try:
            await asyncio.sleep(SLEEP_SECONDS)
        except asyncio.CancelledError:
            break

# TODO criar estrutura de multi agente para criar a automação

if __name__ == "__main__":
    try:
        asyncio.run(daily_loop())
    except KeyboardInterrupt:
        print("Exiting daily runner (KeyboardInterrupt).")
