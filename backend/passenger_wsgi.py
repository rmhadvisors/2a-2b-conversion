"""
Entry point for Hostinger shared-hosting Python apps (Phusion Passenger),
which expects a WSGI callable named `application` in this file.

FastAPI/server.py is an ASGI app, so it's wrapped with a2wsgi. This path is
only needed on Passenger-based shared hosting; skip it entirely on a
VPS/cloud plan where you can run `uvicorn server:app` directly via the
Procfile instead.

Requires: pip install a2wsgi
"""
from a2wsgi import ASGIMiddleware
from server import app as _asgi_app

application = ASGIMiddleware(_asgi_app)
