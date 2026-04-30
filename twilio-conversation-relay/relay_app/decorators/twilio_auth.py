import os
from functools import wraps
from typing import Callable
from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator
from urllib import parse
from relay_app.utils.logger import logger


async def validate_twilio_signature(request: Request, auth_token: str) -> bool:
    twilio_signature = request.headers.get('X-Twilio-Signature')
    logger.debug(f"Twilio signature: {twilio_signature}")

    if not twilio_signature:
        return False

    parsed_url = parse.urlparse(str(request.url))
    base_url = parse.urljoin(str(request.url), parsed_url.path)

    if request.method == "POST":
        form = await request.form()
        params = form
        url = base_url
    else:
        url = base_url + '?' + parse.urlencode(request.query_params)
        params = {}

    logger.debug(f"URL for validation: {url}")
    logger.debug(f"Params for validation: {params}")

    validator = RequestValidator(auth_token)
    is_valid = validator.validate(url, params, twilio_signature)
    logger.debug(f"Signature validation result: {is_valid}")

    return is_valid


def validate_twilio_request(func: Callable):
    """Decorator to validate that HTTP requests are coming from Twilio."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        is_valid = await validate_twilio_signature(request, auth_token)
        if not is_valid:
            logger.warning("Invalid Twilio signature - check URL and AUTH_TOKEN")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
        return await func(request, *args, **kwargs)
    return wrapper
