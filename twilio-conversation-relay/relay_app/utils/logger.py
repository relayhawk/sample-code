import logging
import os
import sys
from contextvars import ContextVar
from typing import Optional

call_sid_context: ContextVar[Optional[str]] = ContextVar('call_sid', default=None)


class CallLogger(logging.Logger):
    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
        call_sid = call_sid_context.get()
        extra = extra or {}
        extra['call_sid'] = f"call_id={call_sid}" if call_sid else "call_id=NO_CALL"
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel + 1)


def setup_logger():
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | '
        '%(call_sid)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logging.setLoggerClass(CallLogger)

    logger = logging.getLogger('relay_app')
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(handler)

    return logger


logger = setup_logger()
