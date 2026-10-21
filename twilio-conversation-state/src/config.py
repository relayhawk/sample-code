"""Settings, read once from the environment (and .env)."""

import os
import secrets
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _host(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").strip().strip("/")


@dataclass(frozen=True)
class Settings:
    public_host: str
    openai_api_key: str | None
    openai_model: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    company_name: str
    tts_provider: str
    tts_voice: str
    enable_recording: bool
    validate_signatures: bool
    dashboard_password: str
    database_path: str
    recordings_dir: str
    session_secret: str

    @property
    def public_base_url(self) -> str:
        return f"https://{self.public_host}"

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def welcome_greeting(self) -> str:
        return f"Hi, thanks for calling {self.company_name}! I just need a few quick details. Can I start with your first name?"


def load_settings() -> Settings:
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    return Settings(
        # NGROK_URL is accepted for parity with the twilio-conversation-relay sample.
        public_host=_host(os.getenv("PUBLIC_URL") or os.getenv("NGROK_URL") or "localhost:8000"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=auth_token,
        twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
        company_name=os.getenv("COMPANY_NAME", "Relay Hawk"),
        tts_provider=os.getenv("TTS_PROVIDER", "ElevenLabs"),
        tts_voice=os.getenv("TTS_VOICE", "ZF6FPAbjXT4488VcRRnw-flash_v2_5-1.2_1.0_1.0"),
        enable_recording=_flag("ENABLE_RECORDING", True),
        # On by default whenever we have an auth token to check against.
        validate_signatures=_flag("VALIDATE_TWILIO_SIGNATURES", bool(auth_token)),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        database_path=os.getenv("DATABASE_PATH", "data/calls.db"),
        recordings_dir=os.getenv("RECORDINGS_DIR", "data/recordings"),
        session_secret=auth_token or os.getenv("SESSION_SECRET") or secrets.token_hex(32),
    )
