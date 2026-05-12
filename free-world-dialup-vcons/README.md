# Free World Dialup — vCons

A minimal sample that demonstrates the full lifecycle of a [vCon](https://datatracker.ietf.org/wg/vcon/about/) (Virtual Conversation) using the [Free World Dialup / Phound](https://fwdialup.com/) SIP platform.

```
┌─────────────┐   SIP/RTP   ┌──────────────────┐   vCon webhook   ┌──────────────┐
│  sip_dialer │ ──────────► │  Free World Dialup│ ───────────────► │   vcon_app   │
│  (baresip)  │             │  (Phound SBC)     │                  │  (FastAPI)   │
└─────────────┘             └──────────────────┘                   └──────┬───────┘
                                                                          │
                                                                   ┌──────▼───────┐
                                                                   │  Browser UI  │
                                                                   │  (vanilla JS)│
                                                                   └──────────────┘
```

1. `sip_dialer` registers to Phound using your SIP credentials and dials an E.164 phone number.
2. After the call completes, Phound POSTs a vCon JSON webhook to your public ngrok URL.
3. `vcon_app` authenticates the webhook, stores the vCon in memory, and exposes it via a simple API.
4. The browser UI polls the API (using a per-session API key stored in `localStorage`) and renders each vCon.

---

## Prerequisites

- [Docker](https://www.docker.com/) + Docker Compose
- [Free World Dialup / Phound](https://fwdialup.com/) account with SIP credentials and a webhook configured to `https://<your-ngrok-domain>/webhook`
- [ngrok](https://ngrok.com/) account with a reserved/branded domain
- macOS (for the `say` audio step below) — or substitute any tool that produces a 16 kHz mono PCM WAV

---

## Quick start

### 1. Clone and configure

```bash
cd free-world-dialup-vcons
cp .env-example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `SIP_SERVER` | Phound SIP server (e.g. `ow-sbc.phound.app:7070`) |
| `SIP_LOGIN` | Your Phound SIP username / extension |
| `SIP_PASSWORD` | Your Phound SIP password |
| `DIAL_TARGET` | Phone number to call in E.164 format (`+15551234567`) |
| `CALL_DURATION_SECONDS` | Seconds to keep the call up (default: 30) |
| `WEBHOOK_SECRET` | Shared secret you configured in the Phound webhook settings |
| `UI_API_KEY` | Leave blank to auto-generate (printed in logs on first start) |
| `NGROK_AUTHTOKEN` | From your ngrok dashboard |
| `NGROK_URL` | Your reserved ngrok domain (e.g. `ngrok.relayhawk.io`) |

### 2. Generate the test audio clip (macOS)

```bash
python3 sip_dialer/make-audio.py
```

This uses macOS `say` + `afconvert` to produce `sip_dialer/audio/test-call.wav` (48 kHz stereo PCM). The WAV is gitignored and must be generated locally before building.

> The extra step beyond `say` is necessary because macOS audio tools add non-standard padding chunks (`FLLR`, `JUNK`) that baresip's `aufile` module rejects. `make-audio.py` strips them.

### 3. Start the server and ngrok

```bash
docker-compose up --build
```

- FastAPI runs on port **5002** (ngrok dashboard on **4041**).
- Watch the `vcon_app` logs for your UI API key:
  ```
  UI API key: <generated-key>
  ```
- Verify the tunnel: `curl https://<NGROK_URL>/healthz`

### 4. Place a call

```bash
docker-compose --profile dial run --rm sip_dialer
```

Or to override the target number:

```bash
docker-compose --profile dial run --rm sip_dialer +15551234567
```

The dialer will:
1. Register to Phound via SIP/UDP
2. Dial the target number
3. Play `test-call.wav` as the caller-side audio
4. Hang up after `CALL_DURATION_SECONDS`

### 5. View the vCon

Open `http://localhost:5002` in your browser. Enter the UI API key when prompted — it's stored in `localStorage` so you only need it once. vCons appear within a few seconds of the call ending.

Or query the API directly:

```bash
curl -H "Authorization: Bearer <UI_API_KEY>" http://localhost:5002/api/vcons | jq .
```

---

## Project structure

```
free-world-dialup-vcons/
├── docker-compose.yml
├── .env-example
├── README.md
└── vcon_app/                        # FastAPI webhook receiver + UI
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                      # Routes: /healthz, /webhook, /api/vcons, /
│   ├── decorators/
│   │   └── webhook_auth.py          # Multi-scheme shared-secret + UI Bearer auth
│   ├── services/
│   │   └── vcon_store.py            # Thread-safe in-memory deque (max 100)
│   ├── static/
│   │   ├── index.html               # Single-page UI
│   │   └── app.js                   # Polling, localStorage API key, card renderer
│   └── utils/
│       └── logger.py                # Structured logger with request_id context
└── sip_dialer/                      # Docker-based baresip CLI dialer
    ├── Dockerfile                   # debian:bookworm-slim + baresip
    ├── entrypoint.sh                # Generates baresip config, dials, hangs up
    └── audio/
        └── test-call.wav            # Generated locally — not committed
```

---

## Security notes

- `.env` is gitignored — never commit credentials.
- The UI HTML page is public (no login), but `/api/vcons` requires `Authorization: Bearer <UI_API_KEY>`.
- `/webhook` accepts the Phound vCon payload only when the shared `WEBHOOK_SECRET` is present.
- Every inbound webhook request (accepted or rejected) is captured to `vcon_app/captured/` for debugging.

---

## Sample vCon payload

Captured live from Free World Dialup — phone numbers redacted. Phound POSTs this as
`Content-Type: application/json` with `x-api-key: <WEBHOOK_SECRET>`.

```json
{
  "vcon": "0.0.1",
  "uuid": "0faa489c-4e5c-11f1-adbb-d3eefda245ef",
  "created_at": "2026-05-12T23:41:17+00:00",
  "parties": [
    {
      "name": "Caller Name",
      "tel": "+1XXXXXXXXXX",
      "mailto": "caller@example.com"
    },
    {
      "name": "Callee Name",
      "tel": "#XXXXX",
      "mailto": "callee@example.com"
    }
  ],
  "dialog": [
    {
      "type": "recording",
      "start": "2026-05-12T23:41:13+00:00",
      "mediatype": "text/plain",
      "duration": 0,
      "body": "No content.",
      "encoding": "UTF8",
      "parties": [1, 0]
    }
  ]
}
```

**Auth:** Phound sends `x-api-key: <WEBHOOK_SECRET>` on every webhook. The receiver
also accepts `Authorization: Bearer <secret>` and other common schemes for compatibility.

---

## Troubleshooting

**No vCon arrives after the call**
- Check `docker-compose logs ngrok` — confirm it connected to your reserved domain.
- Hit `https://<NGROK_URL>/healthz` from an external machine.
- The Phound webhook may batch slightly; wait up to 2–3 minutes after hang-up.

**ngrok fails with `ERR_NGROK_108` (1 simultaneous session limit)**

ngrok free-tier accounts allow only one agent session at a time. If another session
is already active, the Docker sidecar will be rejected. Run ngrok directly on the host
instead:

```bash
# Install once
brew install ngrok/ngrok/ngrok
ngrok config add-authtoken <NGROK_AUTHTOKEN>

# Then start the tunnel (leave running in a separate terminal)
ngrok http --url=<NGROK_URL> http://localhost:5002
```

`docker-compose up` will still start `vcon_app`; just skip (or remove) the `ngrok`
service from `docker-compose.yml` when running natively.

**SIP registration fails**
- `sip_dialer` uses `network_mode: host` to avoid Docker NAT issues with UDP.
- Verify `SIP_SERVER`, `SIP_LOGIN`, `SIP_PASSWORD` match your Phound "Generic SIP" panel.

**`test-call.wav` not found**
- Run `python3 sip_dialer/make-audio.py` (step 2) before building — the file is not committed to the repo.
