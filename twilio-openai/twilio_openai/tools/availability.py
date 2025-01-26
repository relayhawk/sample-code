from datetime import datetime
from typing import Dict, Any

from twilio_openai.protocols.tool import ToolProtocol
from twilio_openai.utils.logger import logger


class AvailabilityTool(ToolProtocol):
    def get_tool_definition(self) -> Dict[str, Any]:
        """Define the availability checking tool configuration."""
        return {
            "type": "function",
            "name": "check_availability",
            "description": "Generates a function that checks availability based on date and time.",
            # "strict": True,
            "parameters": {
                "type": "object",
                "required": ["date", "time"],
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date for which availability is being checked, formatted as YYYY-MM-DD."
                    },
                    "time": {
                        "type": "string",
                        "description": "The time for which availability is being checked, formatted as HH:MM."
                    }
                },
                "additionalProperties": False
            }
        }

    async def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle the availability check request."""
        try:
            date_str = params['date']
            time_str = params['time']

            # Parse the datetime
            datetime_str = f"{date_str} {time_str}"
            requested_datetime = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")

            # Add your availability checking logic here
            # This is a placeholder implementation
            is_available = True  # Replace with actual availability check

            return {
                "available": is_available,
                "datetime": requested_datetime.isoformat(),
                "message": "Available for the requested time" if is_available else "Not available for the requested time"
            }

        except ValueError as e:
            logger.error(f"Invalid date/time format: {e}")
            return {
                "error": "Invalid date/time format",
                "message": str(e)
            }
        except Exception as e:
            logger.error(f"Error checking availability: {e}")
            return {
                "error": "Internal error",
                "message": "Unable to check availability"
            }
