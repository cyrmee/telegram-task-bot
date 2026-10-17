import os
import uvicorn

# Production entrypoint. For local dev use `make up` (docker compose) instead.
if __name__ == "__main__":
    uvicorn.run("bot:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
