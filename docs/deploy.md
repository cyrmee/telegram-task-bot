# Deploying Nest (D2)

Production runs on **Render** (API + dashboard) and **Neon** (Postgres). `render.yaml` in this
repo is the Blueprint for all four services. Nothing here has been created yet. The
[checklist](#checklist-for-zak) at the end is what has to happen in the dashboards.

| | Staging | Production |
|---|---|---|
| Deploys from | `main` (every merge) | `production` branch (moved by a release tag) |
| API | `nest-api-staging` | `nest-api` |
| Dashboard | `nest-dashboard-staging` | `nest-dashboard` |
| Database | Neon branch `staging` | Neon branch `prod` |
| Telegram bots | test bots only | customers' bots |

## Why the API is one always-on instance

The API process also runs the outbox worker (B4), the reminder sweep (B5) and the webhook
monitor (D3). So:

- **Paid plan** (`starter`): the free tier sleeps, and a sleeping API misses Telegram webhooks
  and reminders. That's what happened to `telegram-task-bot-azlx` on 2026-10-08.
- **`numInstances: 1`**, no autoscaling. A second instance would be safe for the outbox (it
  claims rows with `SKIP LOCKED`) but would double the webhook monitor's alerts. Splitting the
  loops into a Render background worker is the path if we ever need more than one API instance.

## Neon

One Neon project, region **AWS eu-central-1 (Frankfurt)**, the same region as the Render services.

- Branch **`prod`** (the default branch), and branch **`staging`** created from it. Reset
  staging from prod whenever you want realistic data. Staging must never hold the only copy of
  anything.
- Each branch has two connection strings. Use both:
  - **Pooled** (host contains `-pooler`): `DATABASE_URL` for the API and dashboard. Many
    short connections go through PgBouncer.
  - **Direct**: `DATABASE_MIGRATION_URL`, used only by the pre-deploy migrations. DDL and
    Alembic's version-table lock shouldn't go through transaction pooling.
- Keep `?sslmode=require` on both. The app adds it if it's missing. Copy the `postgresql://`
  form; the app pins the psycopg2 driver itself.
- Set `prod`'s history retention (point-in-time restore) as long as the Neon plan allows. The free plan's window is short; a paid plan is worth it before customer data lands.

## Render

Apply `render.yaml` as a Blueprint (**New → Blueprint**). Render asks for every `sync: false`
value. The pre-deploy commands run before each new version takes traffic:

- API: `alembic -x url="$DATABASE_MIGRATION_URL" upgrade head`
- Dashboard: `npx auth@1.7.12 migrate` against the direct URL (better-auth's tables, including `jwks`)

If a migration fails, the deploy stops and the previous version keeps serving.

### URLs, CORS and auth

The dashboard and API each need to know the other's public origin. Use the custom domains once
they exist (`app.<domain>`, `api.<domain>`, plus `staging-` variants). Until then use the
`*.onrender.com` URLs Render assigns.

| Service | Variable | Value |
|---|---|---|
| API | `PUBLIC_BASE_URL` | the API's own origin, e.g. `https://api.<domain>`. Telegram webhooks are registered at `<PUBLIC_BASE_URL>/telegram/<bot id>`, and on startup every bot is re-pointed here if it changed |
| API | `CORS_ORIGINS` | the dashboard origin, e.g. `https://app.<domain>` (comma-separated if more than one) |
| API | `AUTH_JWKS_URL` | `https://app.<domain>/api/auth/jwks` |
| API | `AUTH_JWT_ISSUER` | exactly the dashboard's `BETTER_AUTH_URL` |
| Dashboard | `BETTER_AUTH_URL`, `NEXT_PUBLIC_BETTER_AUTH_URL` | the dashboard's own origin |
| Dashboard | `NEXT_PUBLIC_API_URL`, `API_INTERNAL_URL` | the API origin |
| Dashboard | `NEXT_PUBLIC_API_BASE_URL` | `<API origin>/api` (legacy screens, until C4) |

`NEXT_PUBLIC_*` values are baked in at build time. After changing one, use **Manual Deploy →
Clear build cache & deploy**, not a restart.

Changing `PUBLIC_BASE_URL` (e.g. moving to the custom domain) needs only a restart of the API.
It re-registers every connected bot's webhook on startup. Check `/health` and the
`telegram_webhook_unhealthy` alerts afterwards.

### Google sign-in

The OAuth client lives in Google Cloud project **`nest-project-491320`** (APIs & Services →
Credentials). For each environment, add:

- Authorized JavaScript origin: `https://app.<domain>` (and the staging origin)
- Authorized redirect URI: `https://app.<domain>/api/auth/callback/google` (and the staging one)

Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` on both dashboard services. If you'd rather keep
staging logins separate, create a second client for staging. Publish the OAuth consent screen
(or add testers), or only listed test users can sign in.

### Secrets

Generate one of each **per environment**. Staging and production never share a secret.

```bash
# TOKEN_ENCRYPTION_KEY (API): encrypts stored BotFather tokens
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# BETTER_AUTH_SECRET (dashboard): better-auth's signing/encryption secret
openssl rand -base64 32
```

- **`TOKEN_ENCRYPTION_KEY`** can't be changed casually: every stored bot token becomes unreadable,
  and those workspaces have to reconnect their bot. Keep a copy in the password manager.
- **`BETTER_AUTH_SECRET`**: changing it signs everyone out.

### Rotate the keys that lived in local `.env` files

These have been in plain-text `.env` files on dev machines (and the old Render service), so treat
them as exposed before launch:

- **Production bot `@gezat_tasks_bot`**: in @BotFather, `/revoke` the token, then connect the bot to
  Gezat's workspace through the dashboard (Settings → Bot). That stores the new token encrypted
  and sets the webhook. v1 never reads `TELEGRAM_BOT_TOKEN`, so don't set it on Render.
- **Gemini API key**: v1 doesn't use it. Delete the key in Google AI Studio and don't set
  `GEMINI_API_KEY` on Render.
- Anything else that was ever pasted into a local `.env` for Neon/Render: reset the Neon role
  password after the new branches exist, and use the new URLs.

## Releases: staging from `main`, production from tags

Render deploys on branch updates, not tags, so production follows a `production` branch that
only ever moves to a release tag:

```bash
# from an up-to-date main that staging has been running happily
git tag -a v1.0.0 -m "Nest v1.0.0"
git push origin v1.0.0
git push origin v1.0.0^{commit}:refs/heads/production   # triggers the production deploy
```

Do this in **both** repos with the same tag (the dashboard and API are released together). If an
API release includes a migration, deploy the API first. Migrations must stay backward-compatible
with the running dashboard for that window.

To roll back, push the previous tag to `production` (`git push -f origin v0.9.0^{commit}:refs/heads/production`),
or use Render's **Rollback** on the service. Alembic migrations don't roll back automatically. A
release that changes the schema needs a forward fix, or a manual `alembic downgrade` against the
direct URL.

Once CI exists (D1), a GitHub Actions job on `v*` tags can run the tests and push the tag to
`production`, so nobody pushes that branch by hand.

## After the first deploy

- `GET https://api.<domain>/health` returns `{"status": "ok", ...}`. It returns 503 if the DB is
  down or the outbox/reminder loops stall. Point the uptime monitor here (D3).
- Sign up in the dashboard, create the Gezat workspace, connect the bot, invite one member, and
  assign a task. The DM arrives and the Complete button updates the dashboard live.
- Delete the old free services (`telegram-task-bot-azlx`, `nest-dashboard-iq2x`) once nothing
  points at them. Check that no bot webhook still targets them: `getWebhookInfo`, or `/health`
  → `bots`.

## Checklist for Zak

**GitHub**
- [ ] Give Render access to both repos. The API repo is `cyrmee/telegram-task-bot` and the
      dashboard is `zekariasT/nest-dashboard`: either transfer the API repo to your account/org,
      or install the Render GitHub app on `cyrmee` as well.
- [ ] Create the `production` branch in both repos (from the first release tag).

**Neon**
- [ ] Create the project in AWS eu-central-1 (Frankfurt). Rename the default branch to `prod` and
      create a `staging` branch from it.
- [ ] Copy the pooled and direct connection strings for each branch (four URLs).
- [ ] Enable point-in-time restore on `prod`.

**Render**
- [ ] New → Blueprint → this repo. Confirm the four services on the **Starter** plan, Frankfurt.
- [ ] Fill in each service's secrets: Neon URLs, `TOKEN_ENCRYPTION_KEY`, `BETTER_AUTH_SECRET`,
      `PUBLIC_BASE_URL`, `CORS_ORIGINS`, `AUTH_JWKS_URL` / `AUTH_JWT_ISSUER`, the dashboard URLs,
      Google OAuth.
- [ ] After the custom domains are bought, add them to the services and update the URL variables.
      Redeploy the dashboards with a cleared build cache.

**Google Cloud (`nest-project-491320`)**
- [ ] Add the prod and staging origins and `/api/auth/callback/google` redirect URIs. Publish the
      consent screen.

**Keys**
- [ ] `/revoke` the `@gezat_tasks_bot` token in @BotFather, then reconnect it through the
      production dashboard.
- [ ] Delete the old Gemini API key.
- [ ] Reset the Neon role password if an older Neon URL was ever in a `.env` file.
- [ ] Store `TOKEN_ENCRYPTION_KEY` and `BETTER_AUTH_SECRET` (both environments) in the password manager.

**Later (other tasks)**
- [ ] Resend account and a verified sending domain, then `RESEND_API_KEY`, `EMAIL_FROM` and
      `REQUIRE_EMAIL_VERIFICATION=true` on production.
- [ ] Sentry projects, then `SENTRY_DSN` / `NEXT_PUBLIC_SENTRY_DSN` (D3).
- [ ] Uptime monitor on `/health` (D3).
