# Backend (FastAPI) — deploy to Hostinger

GST 2A/2B → Tally purchase-import converter API.

## Local run

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

All paths (`uploads/`, `output/`, `templates/`, `input/`) are resolved
relative to the **current working directory**, not this file's location.
Always run the server with `backend/` as the working directory.

## Deploying to Hostinger

Which setup you need depends on your Hostinger plan:

### VPS / Cloud hosting (you control the process)
Use the `Procfile` as a guide — run:
```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```
with the working directory set to `backend/`. Any process manager
(systemd, pm2, Hostinger's Node/Python app runner) works as long as it
starts the process from inside `backend/`.

### Shared hosting (Passenger-based Python app)
Shared hosting typically expects a WSGI app via Phusion Passenger, but
this backend is ASGI (FastAPI). Use the included `passenger_wsgi.py`,
which wraps the app with `a2wsgi`:

```bash
pip install -r requirements.txt a2wsgi
```

Point Hostinger's Python app "Application startup file" at
`passenger_wsgi.py` and its "Application root" at this `backend/` folder.

## CORS

`server.py` currently allows all origins (`allow_origins=["*"]`) so the
Vercel-hosted frontend can call this API from a different domain. Once
you know your production frontend URL, consider narrowing this to that
specific origin.

## Runtime data

`uploads/`, `output/` hold per-job files (uploaded inputs, generated
Tally imports, logs, error reports). They're gitignored going forward
(placeholder `.gitkeep` files keep the folders present) — make sure
they're writable on the host, and consider periodic cleanup since jobs
accumulate files indefinitely.

## Legacy: serving the frontend from this backend

`server.py` still mounts a built frontend if it finds one at
`frontend/dist` (checked both colocated and as `../frontend/dist`) —
this is a fallback for combined-host deploys and isn't used in the
standard split-deploy (Vercel + Hostinger) setup. Safe to ignore.
