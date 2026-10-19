import os

import uvicorn

# Production entrypoint (`python main.py`). For local dev use `make up` (docker compose) instead.
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
