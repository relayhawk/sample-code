import json
from openai import AsyncOpenAI
from relay_app.utils.logger import logger

TOOLS = [{
    "type": "function",
    "function": {
        "name": "book_meeting",
        "description": "Call when name, phone number, and desired meeting time have all been collected.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone_number": {"type": "string"},
                "meeting_time": {
                    "type": "string",
                    "description": "Caller's desired time, in their words."
                }
            },
            "required": ["name", "phone_number", "meeting_time"]
        }
    }
}]


class OpenAIService:
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def get_reply(self, messages: list) -> tuple[str, bool]:
        """Returns (reply_text, should_end).

        If the model calls book_meeting, returns the confirmation string and should_end=True.
        Otherwise returns the assistant's text reply and should_end=False.
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS,
        )

        choice = response.choices[0]
        logger.debug(f"OpenAI finish_reason: {choice.finish_reason}")

        if choice.finish_reason == "tool_calls":
            tool_call = choice.message.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            name = args.get("name", "")
            phone_number = args.get("phone_number", "")
            meeting_time = args.get("meeting_time", "")
            confirmation = f"Thanks {name}, we'll reach you at {phone_number} at {meeting_time}."
            logger.info(f"book_meeting called: {args}")
            return confirmation, True

        text = choice.message.content or ""
        return text, False
