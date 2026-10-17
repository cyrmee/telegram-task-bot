"""Write docs/openapi.json from the /api/v1 routers and schemas.

    python scripts/export_openapi.py          # regenerate
    python scripts/export_openapi.py --check  # exit 1 if docs/openapi.json is stale

The dashboard generates its types from this file:
    npx openapi-typescript ../telegram-task-bot/docs/openapi.json -o lib/api/schema.d.ts
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from pydantic.json_schema import models_json_schema  # noqa: E402

from app.api.errors import register_error_handlers  # noqa: E402
from app.api.v1 import api_router  # noqa: E402
from app.api.v1.schemas import REALTIME_PAYLOADS  # noqa: E402

OUT = ROOT / "docs" / "openapi.json"


def build_openapi() -> dict:
    app = FastAPI(
        title="Nest API",
        version="1.0.0",
        description="Contract: docs/api-v1.md. Realtime payload schemas are under components.schemas (*Payload).",
        separate_input_output_schemas=False,
    )
    register_error_handlers(app)
    app.include_router(api_router)
    spec = app.openapi()

    # Socket.IO payloads aren't reachable from any route, so add them explicitly.
    _, realtime = models_json_schema(
        [(m, "validation") for m in REALTIME_PAYLOADS],
        by_alias=True,
        ref_template="#/components/schemas/{model}",
    )
    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in realtime.get("$defs", {}).items():
        schemas.setdefault(name, schema)
    return spec


def render() -> str:
    return json.dumps(build_openapi(), indent=2, sort_keys=False) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit("docs/openapi.json is stale: run python scripts/export_openapi.py")
        print("docs/openapi.json is up to date")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT.relative_to(ROOT)}")
