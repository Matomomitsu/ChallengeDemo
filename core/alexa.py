from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.endpoints import chat_endpoint, battery_status, goodwe_api, DEFAULT_STATION_ID, DEFAULT_STATION_NAME

router = APIRouter()


# Modelo para requisição da Alexa
class AlexaRequest(BaseModel):
    intent_name: str
    slots: dict = {}


# Modelo de resposta da Alexa
class AlexaResponse(BaseModel):
    speech: str


@router.post("/alexa", response_model=AlexaResponse)
async def alexa_endpoint(req: AlexaRequest):
    intent = req.intent_name
    slots = req.slots or {}

    try:
        if intent == "GetBatteryStatusIntent":
            # Use o mesmo método battery_status, mas adaptado para retorno de string
            powerstation_id = DEFAULT_STATION_ID
            if not powerstation_id:
                plants = goodwe_api.ListPlants() or {}
                plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
                if not plant_list:
                    return AlexaResponse(speech="Não há plantas configuradas para este usuário.")
                preferred = next((p for p in plant_list if
                                  (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower()),
                                 None)
                powerstation_id = (preferred or plant_list[0]).get("powerstation_id")
            soc_data = goodwe_api.GetSoc(powerstation_id)
            if not soc_data or "soc" not in soc_data or not soc_data["soc"]:
                return AlexaResponse(speech="Não consegui pegar o status da bateria.")
            battery = soc_data["soc"][0]
            speech = f"O status da bateria é: {battery.get('power', 'desconhecido')}W, estado {battery.get('status', 'desconhecido')}."

        elif intent == "ChatIntent":
            user_input = slots.get("user_input", "")
            if not user_input:
                return AlexaResponse(speech="Você não falou nada para eu processar.")
            # Reutiliza o endpoint de chat existente
            response = await chat_endpoint({"user_input": user_input})
            speech = response.response

        elif intent == "GetAlarmsIntent":
            start_date = slots.get("start_date")
            end_date = slots.get("end_date")
            station_name = slots.get("station_name", DEFAULT_STATION_NAME)
            alarms = goodwe_api.GetAlarmsByRange(
                start_date=start_date or "",
                end_date=end_date or "",
                stationname=station_name
            )
            if not alarms or not alarms.get("items"):
                speech = f"Não encontrei alarmes para a planta {station_name}."
            else:
                total = alarms.get("total", 0)
                speech = f"Existem {total} alarmes para a planta {station_name}."

        else:
            speech = "Desculpe, não entendi o comando."

        return AlexaResponse(speech=speech)

    except Exception as e:
        return AlexaResponse(speech=f"Erro ao processar o comando: {str(e)}")
