# Compatibility shim: old start commands run `uvicorn bot:app`. The app lives in app/main.py.
from app.main import app  # noqa: F401
