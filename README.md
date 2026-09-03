# GST 2A/2B → Tally Purchase Import Converter

Monorepo with independently deployable frontend and backend.

```
/frontend   React + Vite app  -> deploy to Vercel
/backend    FastAPI app       -> deploy to Hostinger
```

## Frontend (`frontend/`)

```bash
cd frontend
npm install
npm run dev      # local dev, proxies /api to http://127.0.0.1:8000
npm run build    # outputs frontend/dist
```

**Deploying to Vercel:** point Vercel's project root at `frontend/`
(`vercel.json` inside it sets the build command/output dir and SPA
rewrites). Set the environment variable `VITE_API_BASE_URL` to your
deployed backend's URL (e.g. `https://api.yourdomain.com`) — see
`frontend/.env.example`. Without it, the frontend assumes the API is on
the same origin, which only works for local dev or a combined deploy.

## Backend (`backend/`)

```bash
cd backend
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

**Deploying to Hostinger:** see `backend/README.md` — it covers both
VPS-style (Procfile + uvicorn) and shared-hosting/Passenger
(`passenger_wsgi.py`) setups, plus CORS and runtime-data notes.

## What changed in the restructure (2026-09-01)

All backend source (`main.py`, `server.py`, `converter/`, `lookup/`,
`templates/`, `uploads/`, `output/`, `input/`, `PURCHASE ANALYZER/`,
`Procfile`, `requirements.txt`) moved under `backend/`. No import or
internal path changes were needed — all backend file paths are relative
to the process's working directory, so running from inside `backend/`
keeps everything working as before.

The one functional change: the frontend previously called the API with
a same-origin relative path, which only worked because the backend used
to serve the built frontend itself. Now that they deploy to separate
domains, the frontend uses `VITE_API_BASE_URL` (see above) to reach the
backend cross-origin. Backend CORS already allows all origins.

A full backup of the pre-restructure project (excluding `.venv`,
`node_modules`, `__pycache__`, `.git`) was made before any files moved.
