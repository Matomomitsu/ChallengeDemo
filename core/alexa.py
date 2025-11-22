from fastapi import APIRouter
from api.endpoints import chat_endpoint, goodwe_api, DEFAULT_STATION_ID, DEFAULT_STATION_NAME




router = APIRouter()

def build_alexa_response(speech_text: str, end_session: bool = False):
    """
    Create the Alexa-compatible response envelope.
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
    Entry point for Alexa skill requests.
    """
    try:
        req_type = req.get("request", {}).get("type")

        if req_type == "LaunchRequest":
            return build_alexa_response("Hello! You can ask me about your solar system or smart home.", end_session=False)

        elif req_type == "IntentRequest":
            slots = req["request"]["intent"].get("slots", {})
            user_input = slots.get("user_input", {}).get("value", "")

            if not user_input:
                return build_alexa_response("I couldn't understand what you said.", end_session=False)

            

            # Handle user input
            class ChatRequest:
                def __init__(self, user_input):
                    self.user_input = user_input
                    self.plant_id = None

            chat_req = ChatRequest(user_input)
            response = await chat_endpoint(chat_req)
            return build_alexa_response(response.response, False)

        else:
            return build_alexa_response("Unsupported request.", end_session=True)

    except Exception as e:
        return build_alexa_response(f"Error: {str(e)}", end_session=True)
