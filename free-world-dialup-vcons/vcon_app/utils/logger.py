import logging
import os
import sys
from contextvars import ContextVar
from typing import Optional

request_id_context: ContextVar[Optional[str]] = ContextVar('request_id', default=None)


class RequestLogger(logging.Logger):
    def _log(self, level, msg, args, exc_info=None, extra=None, stack_info=False, stacklevel=1):
        request_id = request_id_context.get()
        extra = extra or {}
        extra['request_id'] = f"req_id={request_id}" if request_id else "req_id=NONE"
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel + 1)


def setup_logger():
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | '
        '%(request_id)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    logging.setLoggerClass(RequestLogger)

    logger = logging.getLogger('vcon_app')
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.addHandler(handler)

    return logger


logger = setup_logger()
