import os
from functools import wraps
from typing import Callable, Union
from fastapi import HTTPException, Request, WebSocket
from twilio.request_validator import RequestValidator
from urllib import parse
from twilio_openai.utils.logger import logger


async def validate_twilio_signature(
    request_obj: Union[Request, WebSocket],
    auth_token: str,
) -> bool:
    """Common validation logic for both HTTP and WebSocket requests.

    This function handles signature validation for both HTTP and WebSocket requests from Twilio.
    The validation process differs based on the request type:

    For WebSocket:
    - Uses 'wss://' scheme regardless of the incoming request scheme
    - Validates against the base URL without query parameters in the URL itself
    - Query parameters are passed separately in the params dict

    For HTTP:
    - POST: Uses base URL without query params, form data passed as params
    - GET: Appends query params to URL, uses empty params dict

    Args:
        request_obj: Either a FastAPI Request or WebSocket object
        auth_token: Twilio auth token used for signature validation

    Returns:
        bool: True if signature is valid, False otherwise
    """
    twilio_signature = request_obj.headers.get('X-Twilio-Signature')
    logger.debug(f"Twilio signature: {twilio_signature}")

    if not twilio_signature:
        return False

    # Handle URL construction differently for WebSocket vs HTTP
    host = request_obj.headers['host']
    is_websocket = isinstance(request_obj, WebSocket)

    if is_websocket:
        # WebSocket connections from Twilio are always signed with 'wss://' scheme
        # This is true even though the initial upgrade request comes as 'https://'
        base_url = f"wss://{host}/media-stream"
        # Query params are passed separately, not appended to URL
        params = dict(request_obj.query_params) if request_obj.query_params else {}
    else:
        # For HTTP requests, we need to handle the URL based on the request method
        parsed_url = parse.urlparse(str(request_obj.url))
        base_url = parse.urljoin(str(request_obj.url), parsed_url.path)

        if request_obj.method == "POST":
            # For POST requests:
            # - URL is the base URL without query params
            # - Form data is passed as params
            url_to_validate = base_url
            form = await request_obj.form()
            params = form  # Keep as FormData object, no need to convert to dict
        else:
            # For GET requests:
            # - Query params are part of the URL
            # - Params dict is empty
            url_to_validate = base_url + '?' + parse.urlencode(request_obj.query_params)
            params = {}

    # Use appropriate URL based on request type
    url = base_url if is_websocket else url_to_validate
    logger.debug(f"URL for validation: {url}")
    logger.debug(f"Params for validation: {params}")

    # Validate the signature using Twilio's validator
    validator = RequestValidator(auth_token)
    is_valid = validator.validate(url, params, twilio_signature)
    logger.debug(f"Signature validation result: {is_valid}")

    return is_valid


def validate_twilio_request(func: Callable):
    """Decorator to validate that HTTP requests are coming from Twilio."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        logger.debug(f"Auth token (first 4 chars): {auth_token[:4] if auth_token else 'None'}")

        is_valid = await validate_twilio_signature(request, auth_token)

        if not is_valid:
            logger.warning("Invalid Twilio signature - check URL and AUTH_TOKEN")
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

        return await func(request, *args, **kwargs)

    return wrapper


def validate_twilio_websocket(func: Callable):
    """Decorator to validate that WebSocket connections are coming from Twilio."""
    @wraps(func)
    async def wrapper(websocket: WebSocket):
        auth_token = os.getenv('TWILIO_AUTH_TOKEN')

        is_valid = await validate_twilio_signature(websocket, auth_token)

        if not is_valid:
            logger.warning("Invalid Twilio signature for WebSocket")
            await websocket.close(code=4403)
            return

        return await func(websocket)

    return wrapper
