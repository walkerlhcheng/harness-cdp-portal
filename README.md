# Harness — CDP Browser Control Portal

A web portal deployed on Railway that connects to a local Chrome CDP browser via Tailscale.

## Features
- 🔐 Secure login portal (session-based auth)
- 🌐 Web-based browser control panel
- 📡 Tailscale-connected to local CDP browser
- 📷 Live screenshots
- 💻 JavaScript console
- 🗂️ Tab management

## Setup

### Environment Variables (Railway)
| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Session signing secret | (required) |
| `ADMIN_USER` | Login username | `admin` |
| `ADMIN_PASS` | Login password | (set a strong one) |
| `CDP_HOST` | Tailscale IP of local machine | `100.113.104.72` |
| `CDP_PORT` | CDP debug port on local machine | `19222` |
| `TS_AUTHKEY` | Tailscale auth key for Railway container | (required) |

### Local Setup
1. Start Chrome with remote debugging: `chrome --remote-debugging-port=9222`
2. Forward port via Tailscale (port 19222 -> 9222)
3. Deploy to Railway with env vars set

## Tech Stack
- **Python 3.11** + **FastAPI** + **uvicorn**
- **uv** for package management
- **Tailscale** for secure tunneling
- **Jinja2** templates
- Deployed on **Railway** via Docker
