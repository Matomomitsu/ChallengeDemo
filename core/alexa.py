from fastapi import APIRouter
from core.llm import call_llm


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

            # Call the LLM directly (same pattern as Google webhook)
            result = await call_llm(user_input, powerstation_id=None, user_ip=None)
            response_text = result.get("response", "")
            return build_alexa_response(response_text, end_session=False)

        else:
            return build_alexa_response("Unsupported request.", end_session=True)

    except Exception as e:
        return build_alexa_response(f"Error: {str(e)}", end_session=True)
