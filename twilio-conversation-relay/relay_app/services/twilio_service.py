from twilio.twiml.voice_response import VoiceResponse, Connect
from relay_app.utils.logger import logger


class TwilioService:
    def get_twiml_conversation_relay(self, ws_url: str) -> str:
        response = VoiceResponse()
        response.say("This call is being recorded for quality and training purposes.")
        connect = Connect()
        connect.conversation_relay(
            url=ws_url,
            welcome_greeting=(
                "Hi! I can help schedule a meeting. "
                "To get started, could I get your name and phone number?"
            ),
        )
        response.append(connect)
        twiml = str(response)
        logger.debug(f"TwiML response: {twiml}")
        return twiml
