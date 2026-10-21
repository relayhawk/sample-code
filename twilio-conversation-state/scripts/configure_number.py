"""
Point your Twilio number at this app.

Sets the number's voice webhook to {PUBLIC_URL}/twiml and its status callback to
{PUBLIC_URL}/twilio/status. Run it again whenever your ngrok URL changes.

    python -m scripts.configure_number            # uses TWILIO_PHONE_NUMBER
    python -m scripts.configure_number --list     # show the numbers on the account
"""

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

API = "https://api.twilio.com/2010-04-01"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--number", default=os.getenv("TWILIO_PHONE_NUMBER"), help="E.164 number, e.g. +15551234567")
    parser.add_argument("--list", action="store_true", help="List the account's numbers and exit")
    args = parser.parse_args()

    sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    host = (os.getenv("PUBLIC_URL") or os.getenv("NGROK_URL") or "").replace("https://", "").replace("http://", "").strip("/")
    if not (sid and token):
        sys.exit("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env")

    with httpx.Client(auth=(sid, token), base_url=f"{API}/Accounts/{sid}", timeout=30) as client:
        numbers = client.get("/IncomingPhoneNumbers.json", params={"PageSize": 100}).raise_for_status().json()
        if args.list:
            for n in numbers["incoming_phone_numbers"]:
                print(f"{n['phone_number']}  {n['friendly_name']:<24}  voice_url={n['voice_url'] or '-'}")
            return
        if not host:
            sys.exit("Set PUBLIC_URL (your ngrok domain) in .env")
        if not args.number:
            sys.exit("Pass --number or set TWILIO_PHONE_NUMBER")

        match = next((n for n in numbers["incoming_phone_numbers"] if n["phone_number"] == args.number), None)
        if match is None:
            sys.exit(f"{args.number} is not on account {sid}. Try --list.")

        client.post(f"/IncomingPhoneNumbers/{match['sid']}.json", data={
            "VoiceUrl": f"https://{host}/twiml",
            "VoiceMethod": "POST",
            "StatusCallback": f"https://{host}/twilio/status",
            "StatusCallbackMethod": "POST",
        }).raise_for_status()
        print(f"{args.number} now sends calls to https://{host}/twiml")
        print(f"Dashboard: https://{host}/")


if __name__ == "__main__":
    main()
