import logging
import os
import sys
from contextvars import ContextVar
from functools import wraps
from typing import Optional

# Create a context variable to store the stream_sid
stream_sid_context: ContextVar[Optional[str]] = ContextVar('stream_sid', default=None)


class StreamLogger(logging.Logger):
    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
        # Get the stream_sid from context
        stream_sid = stream_sid_context.get()

        # Create or update extra dict
        extra = extra or {}
        # Always ensure stream_sid exists in extra, even if None
        extra['stream_sid'] = f"stream_id={stream_sid}" if stream_sid else "stream_id=NO_STREAM"

        # Call parent class _log
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel + 1)


def setup_logger():
    # Get log level from environment variable, default to INFO if not set
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Set up custom formatter with a default value for stream_sid
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | '
        '%(stream_sid)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Register our custom logger class
    logging.setLoggerClass(StreamLogger)

    # Get root logger and set handler
    logger = logging.getLogger('twilio_openai')
    logger.setLevel(log_level)

    # Remove any existing handlers to avoid duplicate logs
    logger.handlers.clear()
    logger.addHandler(handler)

    return logger


def with_stream_sid(func):
    """Decorator to set stream_sid in context for the duration of a function call"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        # Extract stream_sid from the first argument (self) if it exists
        stream_sid = None
        if args and hasattr(args[0], 'stream_sid'):
            stream_sid = args[0].stream_sid

        # Set the stream_sid in context
        token = stream_sid_context.set(stream_sid)
        try:
            return await func(*args, **kwargs)
        finally:
            stream_sid_context.reset(token)
    return wrapper


# Create a singleton logger instance
logger = setup_logger()
