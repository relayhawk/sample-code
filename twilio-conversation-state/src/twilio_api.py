"""
The few Twilio REST calls this template makes, plus webhook signature checks.

Plain httpx against the REST API keeps it obvious what is being sent; the
twilio package is only used for its RequestValidator.
"""

import hashlib
import hmac
import logging
from pathlib import Path

import httpx
from twilio.request_validator import RequestValidator

from src.config import Settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioAPI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._validator = RequestValidator(settings.twilio_auth_token) if settings.twilio_auth_token else None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=(self.settings.twilio_account_sid, self.settings.twilio_auth_token),
            timeout=30,
        )

    def _account_url(self, path: str) -> str:
        return f"{API_BASE}/Accounts/{self.settings.twilio_account_sid}{path}"

    # -- webhook security --------------------------------------------------

    def signature_ok(self, path_and_query: str, params: dict[str, str], signature: str) -> bool:
        """Validate X-Twilio-Signature against the public URL Twilio called."""
        if not self.settings.validate_signatures:
            return True
        if self._validator is None:
            return False
        url = f"{self.settings.public_base_url}{path_and_query}"
        return self._validator.validate(url, params, signature)

    def session_token(self, call_sid: str) -> str:
        """Per-call token we hand to ConversationRelay in TwiML and check on `setup`."""
        return hmac.new(self.settings.session_secret.encode(), call_sid.encode(), hashlib.sha256).hexdigest()

    def session_token_ok(self, call_sid: str, token: str) -> bool:
        return hmac.compare_digest(self.session_token(call_sid), token or "")

    # -- calls ---------------------------------------------------------------

    async def start_recording(self, call_sid: str) -> str:
        """Start a dual-channel recording (caller on one channel, agent on the other)."""
        async with self._client() as client:
            response = await client.post(
                self._account_url(f"/Calls/{call_sid}/Recordings.json"),
                data={
                    "RecordingChannels": "dual",
                    "RecordingStatusCallback": f"{self.settings.public_base_url}/twilio/recording",
                    "RecordingStatusCallbackEvent": "completed",
                    "Trim": "do-not-trim",  # keep timestamps aligned with the event timeline
                },
            )
            response.raise_for_status()
            return response.json()["sid"]

    async def place_call(self, to: str) -> str:
        """Have the agent call someone (handy for demos)."""
        base = self.settings.public_base_url
        async with self._client() as client:
            response = await client.post(
                self._account_url("/Calls.json"),
                data={
                    "To": to,
                    "From": self.settings.twilio_phone_number,
                    "Url": f"{base}/twiml",
                    "StatusCallback": f"{base}/twilio/status",
                    "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                },
            )
            response.raise_for_status()
            return response.json()["sid"]

    async def download_recording(self, recording_sid: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with self._client() as client:
            response = await client.get(self._account_url(f"/Recordings/{recording_sid}.mp3"), follow_redirects=True)
            response.raise_for_status()
            dest.write_bytes(response.content)
        return dest
