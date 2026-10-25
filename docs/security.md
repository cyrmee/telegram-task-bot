# Nest backend security notes (D4)

The D4 pass covers everything in this repo that needs no external system. Items that need Render, Neon, GitHub or legal text are listed under [Open items](#open-items).

## Tenancy

**Model.** A tenant is a workspace.
- Every tenant table carries `workspace_id`, and deleting a workspace cascades to everything it owns.
- The references that matter for isolation are composite `(workspace_id, id)` foreign keys, so the database refuses a cross-workspace reference even if application code gets it wrong:
  - a task's project and its assignee
  - a notification's recipient
  - a task event's task
  - a link invite's member
- Nullable attribution columns (`created_by_member_id`, `completed_by_member_id`, `actor_member_id`, `notifications.task_id`) are single-column because of `ON DELETE SET NULL`. The services that write them check the workspace first (e.g. `outbox.enqueue` verifies the task's workspace).

**Dashboard API.**
- Every `/api/v1/workspaces/{workspaceId}/…` route depends on `require_manager`. It resolves the caller's *active* membership from the JWT `sub`. If there is none, it returns 404, so callers can't tell which workspace ids exist.
- `tests/test_tenancy_sweep.py` reads the OpenAPI spec and calls **every** workspace route as a manager of another workspace (expecting 404) and without a token (expecting 401). New routes are covered automatically.
- Services take the workspace id from the guard, never from the request body, and look up child rows with `workspace_id = :ws AND id = :id`.

**Bot.**
- The workspace comes from which bot received the update: `POST /telegram/{botId}`, with the per-bot secret header checked in constant time.
- Members are looked up by `(workspace_id, telegram_user_id)`.
- Handlers only answer private chats.
- `complete_task` itself refuses anyone who isn't the assignee or a manager of that workspace.

**Realtime.** Socket.IO connections need the same JWT. Rooms are joined only by the server (`ws:{id}` for workspaces where the caller is an active manager). Demoted or removed managers are taken out of the room.

**Review (2026-10-25).** I read every query in `app/services`, `app/api`, `app/bot`, `app/notifications` and `app/scheduler`. Lookups by bare id fall into three groups, all safe:
- re-reads of a row that's already scoped
- follows of a composite FK
- system workers that run across all workspaces by design (outbox worker, reminder sweep)

I found no cross-tenant read or write path. Findings:

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | High | A removed manager could get the manager role back by redeeming any team invite (rejoin kept the old role) | Fixed (`f699fce`) |
| 2 | Medium | Link invites still worked after their target member was removed | Fixed (`f699fce`) |
| 3 | Medium | Last-manager check could be bypassed by two concurrent demotions | Fixed: manager rows are locked (`f699fce`) |
| 4 | Low | `/telegram/{botId}` answers 404 for an unknown bot and 403 for a bad secret, which reveals which bot ids are connected (bot ids are public anyway) | Reported to Stream B: answer 403 for both |
| 5 | Low | The webhook parses the JSON body before checking the secret | Reported to Stream B: check the header first. The rate limit caps the cost meanwhile |
| 6 | Info | Legacy single-bot code (`app/bot/legacy`) has commands that act on any task by code | Off by default (`LEGACY_BOT_ENABLED`); B6 drops those commands |

## Authentication

- Dashboard users authenticate to the API with the better-auth **JWT plugin** (EdDSA by default).
- The API verifies signature, `exp`, `iss`, `aud` and `sub` against the dashboard's JWKS. Keys are cached and refetched when a token has an unknown `kid`.
- Only asymmetric algorithms are accepted, so `none` and HS256 are rejected (tested).
- The API never reads better-auth's session tables.
- CORS allows only `CORS_ORIGINS`, without credentials.

## Rate limiting

`app/ratelimit.py` wraps the whole ASGI app (REST, webhooks, Socket.IO) with per-client-IP fixed windows:

| Rule | Limit |
|---|---|
| `POST /api/v1/workspaces` | 10 / minute |
| `/api/*` (everything else) | 300 / minute |
| `/telegram/*` | 1200 / minute (Telegram's servers share few IPs) |
| `/socket.io` | 60 / minute |

- Over the limit, the API returns **429** with the error envelope and `Retry-After`; WebSocket connections are closed with code 1008.
- The client IP comes from uvicorn's `--proxy-headers` (set in `render.yaml`).
- Counters live in the process, which fits the single API instance D2 runs. Running more than one instance would mean moving them to Redis.
- `RATE_LIMIT_ENABLED=false` turns the limiter off.

## Secrets

- **Bot tokens:**
  - stored only as Fernet ciphertext in `workspace_bots.token_encrypted`
  - never returned by the API
  - redacted from every log line and traceback by `app/logging_config.py`; httpx request logging, which leaked them, is muted
  - scrubbed from Sentry events (D3)
- **Webhook secrets:** random per bot, compared in constant time.
- **Invite tokens:** `secrets.token_urlsafe(24)` (192 bits). Each is either single-use or capped, always expires, and can be revoked. Removing a member revokes the invites they created and their link invites.

### Rotating `TOKEN_ENCRYPTION_KEY`

Do this when the key may have leaked, when someone with access leaves, or yearly. Each environment has its own key.

> Needs multi-key support in `app/bot/crypto.py` (`MultiFernet`, comma-separated keys, newest first), which has been requested from Stream B. Until it lands, changing the key makes stored tokens unreadable, and every workspace would have to reconnect its bot.

1. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set `TOKEN_ENCRYPTION_KEY=<new>,<old>` (new first) and deploy. New tokens are encrypted with the new key, and old ones still decrypt.
3. Re-encrypt the stored tokens: `python scripts/rotate_token_key.py --dry-run`, then without `--dry-run`. It runs in one transaction.
4. Set `TOKEN_ENCRYPTION_KEY=<new>` and deploy.
5. If the old key leaked, also ask workspaces to revoke and reissue their BotFather tokens. Rotating our key doesn't protect tokens that were already stolen.

## Dependency audit

- **Backend:** `pip-audit -r requirements.txt` runs in CI (backend workflow, `audit` job).
- The first run found 7 known vulnerabilities: Starlette 0.48.0 (6) and python-socketio 5.16.1 (1). They were cleared by moving to FastAPI 0.162.0, Starlette 1.10.0 and python-socketio 5.16.2 (`9744c4c`). It's clean as of 2026-10-25.
- **Dashboard:** `npm audit --audit-level=high --omit=dev` is in the dashboard workflow, which Stream C commits.
- Dependabot opens weekly pip and npm update PRs.

## Open items

- Push to GitHub and turn on branch protection that requires the backend and dashboard checks (needs Zak's OK).
- Production secrets in Render, separate per environment, plus rotating the old bot token and the Gemini key before launch (D2, needs Zak's OK).
- Privacy policy and terms pages, needed for Google OAuth verification (Stream C / Zak).
- ~~Data export and workspace deletion endpoints~~: done (`GET /workspaces/{id}/export`, `DELETE /workspaces/{id}` with name confirmation and an audit log line).
- Run `/security-review` on both repos once the code freezes.
