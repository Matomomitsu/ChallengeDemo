from fastapi import APIRouter
from pydantic import BaseModel
from api.endpoints import chat_endpoint, goodwe_api, DEFAULT_STATION_ID, DEFAULT_STATION_NAME

router = APIRouter()

# Função para criar resposta no formato da Alexa
def build_alexa_response(speech_text: str, end_session: bool = False):
    return {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "PlainText",
                "text": speech_text
            },
            "shouldEndSession": end_session
        }
    }

@router.post("/alexa")
async def alexa_endpoint(req: dict):
    try:
        req_type = req.get("request", {}).get("type")

        if req_type == "LaunchRequest":
            return build_alexa_response("Olá! O que você quer fazer?", end_session=False)

        elif req_type == "IntentRequest":
            intent_name = req["request"]["intent"]["name"]
            slots = req["request"]["intent"].get("slots", {})

            if intent_name == "GetBatteryStatusIntent":
                powerstation_id = DEFAULT_STATION_ID
                if not powerstation_id:
                    plants = goodwe_api.ListPlants() or {}
                    plant_list = plants.get("plants", []) if isinstance(plants, dict) else []
                    if not plant_list:
                        return build_alexa_response("Não há plantas configuradas para este usuário.", True)
                    preferred = next((p for p in plant_list if
                                      (p.get("stationname") or "").strip().lower() == DEFAULT_STATION_NAME.strip().lower()),
                                     None)
                    powerstation_id = (preferred or plant_list[0]).get("powerstation_id")

                soc_data = goodwe_api.GetSoc(powerstation_id)
                if not soc_data or "soc" not in soc_data or not soc_data["soc"]:
                    return build_alexa_response("Não consegui pegar o status da bateria.", True)

                battery = soc_data["soc"][0]
                speech = f"O status da bateria é: {battery.get('power', 'desconhecido')}W, estado {battery.get('status', 'desconhecido')}."
                return build_alexa_response(speech, True)

            elif intent_name == "ChatIntent":
                user_input = slots.get("user_input", {}).get("value", "")
                if not user_input:
                    return build_alexa_response("Você não falou nada para eu processar.", True)

                class ChatRequest:
                    def __init__(self, user_input):
                        self.user_input = user_input

                chat_req = ChatRequest(user_input)
                response = await chat_endpoint(chat_req)
                return build_alexa_response(response.response, True)

            else:
                return build_alexa_response("Desculpe, não entendi o comando.", True)

        else:
            return build_alexa_response("Tipo de requisição não suportado.", True)

    except Exception as e:
        return build_alexa_response(f"Erro ao processar o comando: {str(e)}", True)
