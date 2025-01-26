from abc import ABC, abstractmethod
from typing import Dict, Any


class ToolProtocol(ABC):
    """Protocol for implementing OpenAI tools.

    All tools must implement:
    - get_tool_definition: Returns the OpenAI tool configuration
    - handle: Implements the actual tool functionality
    """

    @abstractmethod
    async def handle(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle the tool call with given parameters.

        Args:
            params: Parameters passed from OpenAI tool call

        Returns:
            Response to be sent back to OpenAI
        """
        pass

    @abstractmethod
    def get_tool_definition(self) -> Dict[str, Any]:
        """Get the tool definition for OpenAI.

        Returns:
            Tool configuration following OpenAI's function calling format
        """
        pass
