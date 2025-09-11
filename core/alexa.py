from fastapi import APIRouter
from api.endpoints import chat_endpoint, goodwe_api, DEFAULT_STATION_ID, DEFAULT_STATION_NAME



router = APIRouter()

def build_alexa_response(speech_text: str, end_session: bool = False):
    """
    Cria a resposta no formato que a Alexa espera.
    """
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
    """
    Endpoint principal para receber requisições da Alexa.
    """
    try:
        req_type = req.get("request", {}).get("type")

        # --- Quando o usuário abre a skill ---
        if req_type == "LaunchRequest":
            return build_alexa_response("Oi! Pode me perguntar qualquer coisa.", end_session=False)

        # --- Quando o usuário fala algo ---
        elif req_type == "IntentRequest":
            slots = req["request"]["intent"].get("slots", {})
            user_input = slots.get("user_input", {}).get("value", "")

            if not user_input:
                return build_alexa_response("Não entendi o que você disse.", end_session=False)

            # Trata a entrada do usuário
            class ChatRequest:
                def __init__(self, user_input):
                    self.user_input = user_input

            chat_req = ChatRequest(user_input)
            response = await chat_endpoint(chat_req)
            return build_alexa_response(response.response, False)

        # --- Outros tipos de requisição não suportados ---
        else:
            return build_alexa_response("Requisição não suportada.", end_session=True)

    except Exception as e:
        return build_alexa_response(f"Erro: {str(e)}", end_session=True)
