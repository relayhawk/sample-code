import base64
from functools import wraps

from fastapi import HTTPException, Request


def _check_secret(request: Request, secret: str) -> bool:
    """Return True if the shared secret appears via any common transport scheme."""
    # Authorization: Bearer <secret>  or  Authorization: <secret>
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == secret:
        return True
    if auth.strip() == secret:
        return True

    # HTTP Basic auth — accept if password field matches
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
            _, _, password = decoded.partition(":")
            if password == secret:
                return True
        except Exception:
            pass

    # Common custom headers
    for header in ("x-webhook-secret", "x-webhook-key", "x-auth-token", "x-api-key"):
        if request.headers.get(header, "").strip() == secret:
            return True

    # Query params
    for param in ("auth", "key", "secret", "token"):
        if request.query_params.get(param, "").strip() == secret:
            return True

    return False


def validate_webhook(secret: str):
    """FastAPI dependency factory — raises 403 if shared secret not found."""
    async def dependency(request: Request):
        if not _check_secret(request, secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    return dependency


def validate_ui_key(api_key: str):
    """FastAPI dependency factory — raises 401 if UI API key not present."""
    async def dependency(request: Request):
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == api_key:
            return
        raise HTTPException(status_code=401, detail="Invalid API key")
    return dependency
