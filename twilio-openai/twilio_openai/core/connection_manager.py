import asyncio
from starlette.websockets import WebSocketDisconnect
from twilio_openai.protocols.websocket_adapter import WebsocketAdapterProtocol
from twilio_openai.utils.logger import logger


class ConnectionManager:
    """Generic manager for bidirectional WebSocket streaming."""

    @staticmethod
    async def process_streams(handler: WebsocketAdapterProtocol):
        """Process bidirectional streams between two WebSocket connections.

        Creates two async tasks:
        1. receive_stream: Handles messages from source to target service
        2. send_stream: Handles messages from target back to source service

        The manager will run until either stream ends or encounters an error.

        Args:
            handler: Adapter that implements message translation between services
        """
        try:
            # Start task for source -> target message flow
            logger.debug("Creating receive task")
            receive_task = asyncio.create_task(
                handler.receive_stream(),
                name='receive_task'
            )

            # Start task for target -> source message flow
            logger.debug("Creating send task")
            send_task = asyncio.create_task(
                handler.send_stream(),
                name='send_task'
            )

            logger.debug("Waiting for either task to complete")
            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            logger.debug("Stream tasks completed")

            # Clean up the remaining direction if one side disconnects
            for task in pending:
                logger.debug(f"Cancelling pending task: {task.get_name()}")
                task.cancel()

            # Give pending tasks a chance to clean up
            if pending:
                try:
                    await asyncio.wait(pending, timeout=0.5)
                except asyncio.TimeoutError:
                    logger.warning("Timeout while waiting for graceful shutdown")

            # Check if either task failed with an error
            for task in done:
                try:
                    await task
                except WebSocketDisconnect:
                    logger.debug("Normal WebSocket disconnection")
                except Exception as e:
                    logger.error(f"Task failed with error: {e}")
                    raise

        except Exception as e:
            logger.error(f"Error in process_streams: {e}")
            raise
        finally:
            # Only close the target WebSocket - let FastAPI handle the source
            try:
                await handler.close()
                logger.debug("Closed WebSocket connections")
            except Exception as e:
                logger.error(f"Error during connection cleanup: {e}")
