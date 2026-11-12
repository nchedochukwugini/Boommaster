# BoomMaster

BoomMaster is an AI music creation mini app. Choose a musical direction, review and edit generated lyrics, pay for a track with NIM or USDT, then follow production until the finished song is ready to play or download.

**Live app:** [boommaster.vercel.app](https://boommaster.vercel.app)  
**Backend health:** [boommaster-api.onrender.com/api/health](https://boommaster-api.onrender.com/api/health)

## How it works

1. Connect a Nimiq wallet and create a profile tied to that wallet.
2. Select up to three styles and three moods, and describe the idea for the song.
3. Review the generated lyrics, suggested title, and A&R feedback. Edit the title or lyrics as needed.
4. Pay the displayed price using NIM or Polygon USDT. The backend verifies the transaction before starting production.
5. Track the generation status and play or download the finished song. Saved creations and playbooks belong to the connected wallet.

The app charges **0.50 USDT per track** or its live NIM equivalent. Profile photos are stored in the current browser.

## Project layout

- `index.html` — single-file mini app frontend, including its styles and JavaScript.
- `app.py` — Flask API for wallet authentication, profiles, lyrics, payment verification, song generation, and saved work.
- `bot.py` — separate Telegram bot entry point; it is not part of the deployed mini app flow.
- `requirements.txt` — Python dependencies for the backend.
- `render.yaml` — Render backend service configuration.
- `vercel.json` — routes frontend `/api/*` requests to the Flask backend.

## Architecture

The frontend is hosted on Vercel and is designed to open inside Nimiq Pay. It connects to the Nimiq Mini App SDK for NIM wallet access and transactions, and uses the browser's Polygon wallet provider for USDT payments. API requests pass through Vercel to the Flask service hosted on Render.

The backend stores account and creation data in Neon Postgres, uses OpenRouter for text generation, and connects to the Audiera music generation API for song production. Long-running song generation is tracked by polling the backend status endpoint, so the browser does not need to keep a single request open while a track is being made.

## Run locally

Use Python 3.11 or newer.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The Flask app runs at `http://127.0.0.1:8765` by default. Configure the backend secrets and database connection in a local `.env` file before starting it. Required deployment variables include:

- `DATABASE_URL` — Neon Postgres connection string.
- `AUDIERA_API_KEY` — music generation API key.
- `OPENROUTER_API_KEY` — text generation API key.

Keep `.env` and secret values private; do not commit them to the repository.

To preview the frontend, serve the project directory with a local static HTTP server and open it in a browser. Wallet features require the appropriate wallet provider; the intended end-to-end payment experience runs inside Nimiq Pay.

## Deployment

### Backend on Render

The repository includes `render.yaml` for the Flask service. Create or update the Render service from this repository, provide the required environment variables in Render, and attach the Neon database using its `DATABASE_URL`. The configured health check is `/api/health`.

### Frontend on Vercel

Import the repository into Vercel and deploy the static frontend. `vercel.json` rewrites `/api/*` calls to the Render backend. Update that rewrite destination if the backend URL changes, then redeploy the frontend.

After deployment, open the Vercel URL from **Nimiq Pay → Mini Apps** to use wallet connection and payment features.
