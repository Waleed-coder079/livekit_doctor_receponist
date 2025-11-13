# Doctor Receptionist Voice Agent

A LiveKit-powered voice agent that acts as a clinic receptionist. It books appointments for two branches (Sialkot and Lahore) with day-based availability, confirms details with patients, and speaks via TTS in realtime.

## Features

- 🎤 Natural voice conversations (low latency, barge-in)
- 🗓️ Branch/day-aware availability (Sialkot: Mon–Wed, Lahore: Thu–Sat)
- ⏰ Fixed one-hour slots (10–2, 4–8)
- 🧰 Function tools: check availability, book appointment, list bookings
- 🔌 Providers: Deepgram STT, Google Gemini LLM, ElevenLabs TTS, Silero VAD

## Prerequisites

- Python 3.9 or later
- API Keys:
    - Deepgram API key (STT)
    - Google Gemini API key (LLM)
    - ElevenLabs API key (TTS)
    - LiveKit Cloud credentials (optional; only for cloud deployment)

## Quick Start

### 1) Install dependencies

```powershell
python -m venv .\venv
.\venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -U livekit-agents[mcp] livekit-plugins-deepgram livekit-plugins-silero livekit-plugins-turn-detector `
    livekit-plugins-google livekit-plugins-elevenlabs python-dotenv
```

### 2) Configure environment

```powershell
copy .env.example .env
# Then edit .env and add your keys
```

Required: `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`

### 3) Run the agent

```powershell
# Using Python directly
python .\main.py console

# Or via the PowerShell helper (if configured to run main.py)
.\start-agent.ps1 -Mode console
```

## Architecture

```
┌─────────────┐     ┌────────────────────────────┐
│   Microphone│──▶──│  LiveKit AgentSession      │──▶ ElevenLabs TTS (voice out)
│ + Speakers  │     │  (STT+LLM+TTS+VAD pipeline)│
└─────────────┘     └───────┬─────────┬──────────┘
                                                         │         │
                                                 Deepgram    Gemini
                                                        STT        LLM
```

## Code Overview

- Entry point: `main.py`
- Agent class: `DoctorReceptionist`
- Tools:
    - `get_current_date_and_time`
    - `check_availability(city, day)`
    - `book_appointment(patient_name, city, day, slot)`
    - `show_appointments()`

## Voice Pipeline Configuration

- STT: Deepgram Nova-2 (`DEEPGRAM_API_KEY`, `STT_LANGUAGE=en`)
- LLM: Google Gemini (`GEMINI_API_KEY`, `LLM_CHOICE=gemini-2.5-flash` default)
- TTS: ElevenLabs (`ELEVENLABS_API_KEY`, optional `ELEVENLABS_VOICE_ID`, `ELEVENLABS_TTS_MODEL`)
- VAD: Silero (bundled via plugin, no extra keys)

## Troubleshooting

- 401 to LiveKit Inference STT: This project uses Deepgram STT locally. If you switch to `inference.STT`, you must set valid LiveKit Cloud credentials.
- Transient STT disconnects: Usually network/provider hiccups. Retrying or maintaining interaction often resolves.
- Audio device issues: Check microphone/speaker permissions in your OS.

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPGRAM_API_KEY` | Yes | Deepgram API key for STT |
| `GEMINI_API_KEY` | Yes | Google Gemini API key for LLM |
| `ELEVENLABS_API_KEY` | Yes | ElevenLabs API key for TTS |
| `ELEVENLABS_VOICE_ID` | No | ElevenLabs voice ID (optional) |
| `ELEVENLABS_TTS_MODEL` | No | ElevenLabs TTS model (optional) |
| `ELEVENLABS_STREAMING_LATENCY` | No | Integer latency hint (default 0) |
| `STT_LANGUAGE` | No | STT language code (default `en`) |
| `LLM_CHOICE` | No | LLM model (default `gemini-2.5-flash`) |
| `LIVEKIT_URL` | No | LiveKit server URL (for cloud) |
| `LIVEKIT_API_KEY` | No | LiveKit API key (for cloud) |
| `LIVEKIT_API_SECRET` | No | LiveKit API secret (for cloud) |
| `LOG_LEVEL` | No | Logging level (default `INFO`) |

## Resources

- LiveKit Agents: https://docs.livekit.io/agents/
- LiveKit Python SDK: https://github.com/livekit/agents
