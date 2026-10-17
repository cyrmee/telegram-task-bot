"""FastAPI app factory: `uvicorn app.main:app`.

A1 wires in the bot lifespan (app/bot) and retires bot.py.
"""
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app import db
from app.api.errors import register_error_handlers
from app.api.v1 import api_router
from app.config import get_settings
from app.realtime import sio


def create_app() -> socketio.ASGIApp:
    settings = get_settings()
    api = FastAPI(title="Nest API", version="1.0.0", separate_input_output_schemas=False)
    register_error_handlers(api)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, no cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    api.include_router(api_router)

    @api.get("/health", tags=["health"], include_in_schema=False)
    def health():
        with db.SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    # Socket.IO handles /socket.io/*, everything else goes to FastAPI (tests reach it as app.other_asgi_app).
    return socketio.ASGIApp(sio, other_asgi_app=api, socketio_path="socket.io")


app = create_app()
