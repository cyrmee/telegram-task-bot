# Nest API v1 contract

This is the contract between the backend (`telegram-task-bot`, Streams A and B) and the dashboard (`nest-dashboard`, Stream C). Stream A owns it. **Update this file in the same commit as any endpoint change.**

- **Machine-readable contract:** [`docs/openapi.json`](openapi.json), generated from `app/api/v1/schemas.py` and the routers by `python scripts/export_openapi.py`. A test fails if it's stale. Where this file and the OpenAPI spec disagree on a field, the OpenAPI spec wins. Fix this file.
- **Status:** every route below exists with its final path, parameters and response model, but returns `501 not_implemented` until A2–A6 land. Build against the types now.
- **Data model:** [`docs/data-model.md`](data-model.md).

## 1. Conventions

| Topic | Rule |
|---|---|
| Base URL | `{NEXT_PUBLIC_API_BASE_URL}/api/v1`. Dev: `http://localhost:8001/api/v1` (nest 1) or `:8000` (nest 2). |
| Format | JSON, UTF-8. **camelCase** keys in requests and responses. |
| IDs | UUID strings (`"3f0c…"`). One exception: better-auth user ids (`user.id`, `member.userId`) are opaque strings. Tasks also have `number` (an int unique per workspace) for display (`#12`) and `/done 12`. |
| Timestamps | Responses use ISO-8601 **UTC** with a `Z` and milliseconds: `"2026-10-21T06:00:00.000Z"`. Requests must include an offset (`Z` or `+03:00`); naive times are rejected with 422. The client converts to the workspace timezone (`workspace.timezone`, an IANA name) for display. |
| Dates | `YYYY-MM-DD`, interpreted in the workspace timezone (only analytics `from`/`to`). |
| Nulls | Optional fields are always present in responses, as `null` when empty. In `PATCH`, an omitted field means unchanged and `null` clears it (title and other required fields can't be cleared). |
| Lists | `{"items": [...]}`. Paginated lists (tasks) add `page`, `pageSize` and `total`. `page` starts at 1, `pageSize` defaults to 50 with a max of 200. |
| Repeated filters | Repeat the query parameter: `?projectId=a&projectId=b`. |
| Status codes | `200` read or update, `201` create (body is the created resource), `204` delete (no body), `4xx`/`5xx` with the error body (§4). |
| Idempotency | `complete`, `reopen`, `archive`, `unarchive` and invite revoke are idempotent: repeating one returns the current state and sends no second notification. |
| Request ID | Send `X-Request-ID` and it's echoed back in errors as `requestId`. |
| Versioning | Breaking changes go under `/api/v2`. Adding fields or endpoints isn't breaking, so clients must ignore unknown fields. |

## 2. Auth

### Dashboard (Stream C, C2)

Enable the better-auth **JWT plugin** (`better-auth/plugins`, v1.5):

```ts
// lib/auth.ts
import { jwt } from "better-auth/plugins";
betterAuth({
  // ...
  plugins: [
    jwt({
      jwt: {
        issuer: process.env.BETTER_AUTH_URL,      // e.g. http://localhost:3000
        audience: "nest-api",
        expirationTime: "15m",
        definePayload: ({ user }) => ({ email: user.email, name: user.name, image: user.image }),
      },
      // keys: EdDSA / Ed25519 by default; leave as is
    }),
  ],
});

// lib/auth-client.ts
import { jwtClient } from "better-auth/client/plugins";
createAuthClient({ baseURL, plugins: [jwtClient()] });
```

- The plugin adds a `jwks` table. Create it with `npx @better-auth/cli migrate`. Alembic never touches better-auth's tables.
- It exposes `GET /api/auth/jwks` (public keys) and `GET /api/auth/token` (returns `{ token }` for the current session cookie).
- To get a token, call `authClient.token()`. Cache the token in memory, **never** in localStorage, and refresh it when it's under about 60 s from `exp`, or once on a 401 before redirecting to login.
- Send it on every API call as `Authorization: Bearer <jwt>`.
- Remove the mock `POST /api/auth/login` (backend) and every caller of it.

### API (Stream A, A2)

- `current_user` verifies the JWT signature against `AUTH_JWKS_URL` (e.g. `http://localhost:3000/api/auth/jwks`), with keys cached and refetched on an unknown `kid`. It checks `exp` (with 30 s leeway), `iss == AUTH_JWT_ISSUER` and `aud == AUTH_JWT_AUDIENCE` (`nest-api`). The algorithm comes from the JWK (EdDSA by default); `none` and HS* are never accepted.
- The user id is `sub` (= better-auth `user.id`).
- Any failure returns **401** `unauthenticated`. The API never reads better-auth's session tables.
- **CORS:** only the origins in `CORS_ORIGINS` (comma-separated), with credentials off. Bearer tokens don't need cookies.

New backend env vars (for 0.1's `.env.example`): `AUTH_JWKS_URL`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `CORS_ORIGINS`.

### Not for the dashboard

- `POST /telegram/{botId}` is the Telegram webhook (Stream B, B1). It's authenticated by the `X-Telegram-Bot-Api-Secret-Token` header, not a JWT.
- `GET /health` is unauthenticated and pings the DB (A1).

## 3. Tenancy and roles

- Every resource lives under `/api/v1/workspaces/{workspaceId}/…`. The only exceptions are `GET /me` and `POST /workspaces`.
- The guard looks up the caller's `members` row for `{workspaceId}` (`user_id = sub`, `status = active`):
  - No row: **404** `not_found`, so callers can't learn which workspace ids exist.
  - Role too low: **403** `forbidden`.
- **v1:** every workspace route requires `role = manager`. Members use Telegram only (UX map: "Team members never need the dashboard").
- A resource id from another workspace is a 404, even if it exists. The DB also enforces this with composite foreign keys.

## 4. Errors

Every non-2xx response:

```json
{
  "error": {
    "code": "member_has_open_tasks",
    "message": "Hana has 3 pending tasks. Choose who takes them over.",
    "details": { "openTaskCount": 3 },
    "requestId": "c0a8…"
  }
}
```

`code` is stable, so switch on it. `message` is plain English and safe to show to a manager. `details` is optional.

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed request (not a validation issue) |
| 401 | `unauthenticated` | Missing, invalid or expired JWT |
| 403 | `forbidden` | Member of the workspace, but the role is too low |
| 404 | `not_found` | Unknown route, resource not in this workspace, or not a member |
| 409 | `bot_in_use` | `PUT /bot`: that bot is live in another workspace |
| 409 | `bot_not_connected` | Creating an invite with no active bot |
| 409 | `member_exists` | Adding a @username that's already a member |
| 409 | `member_has_open_tasks` | Removing a member with pending tasks and no `reassignTo` |
| 409 | `last_manager` | Demoting or removing the last manager |
| 409 | `project_not_empty` | Deleting a project that still has tasks (archive it instead) |
| 422 | `validation_error` | `details.fields: [{field, message}]`, where `field` is a camelCase path such as `dueAt` |
| 422 | `invalid_bot_token` | Telegram `getMe` rejected the token |
| 422 | `project_archived` | Creating or moving a task into an archived project |
| 422 | `assignee_not_active` | Assigning to a removed member, or `reassignTo` is invalid |
| 422 | `invalid_timezone` | Not an IANA timezone name |
| 429 | `rate_limited` | Too many requests (D4). Respect `Retry-After` |
| 501 | `not_implemented` | In the contract but not built yet |
| 502 | `telegram_unavailable` | Telegram API down or timed out (bot connect/status) |
| 500 | `internal_error` | Bug. Report it with the `requestId` |

## 5. Endpoints

All paths are relative to `/api/v1`. **M** = manager only.

### Me and workspaces

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/me` | → `Me` | The user plus their workspaces, each with `role` and `onboarding {hasBot, memberCount, projectCount, taskCount}`. Drives the C3 stepper and home checklist. Any signed-in user can call it. |
| POST | `/workspaces` | `WorkspaceCreate {name, timezone}` → `Workspace` 201 | The caller becomes the workspace's first manager. Any signed-in user can call it. |
| GET | `/workspaces/{workspaceId}` | → `Workspace` | **M** |
| PATCH | `/workspaces/{workspaceId}` | `WorkspaceUpdate {name?, timezone?, reminderLeadHours? (0–720)}` → `Workspace` | **M**. Changing `reminderLeadHours` reschedules pending reminders (B5). |

### Bot

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/bot` | → `Bot` | **M**. `status` is `not_connected`, `active`, `invalid` or `disconnected`. `webhook` comes live from `getWebhookInfo` (null unless active). |
| PUT | `/workspaces/{id}/bot` | `BotConnect {token}` → `Bot` | **M**. Validates with `getMe`, rejects a bot that's live elsewhere (409 `bot_in_use`), encrypts the token, sets the webhook and commands. Replaces any current bot. The token is never returned or logged. |
| DELETE | `/workspaces/{id}/bot` | → 204 | **M**. Deletes the webhook and marks the bot `disconnected`. Members stop getting DMs, and queued notifications wait until a bot is connected again. |

Stream A exposes this over HTTP; Stream B's `BotManager` does the work (A3 ↔ B1).

### Members

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/members?status=&role=` | → `{items: Member[]}` | **M**. By default excludes `removed`. `Member` has `displayName`, `telegram {userId, username, firstName, lastName}` or null, `invitedUsername`, `userId`, `email`, `status`, `openTaskCount`. |
| POST | `/workspaces/{id}/members` | `{telegramUsername}` → `Member` 201 | **M**, optional (A5). Creates a `pending` member, matched by username when they `/start` the bot. Telegram can't DM them before that. |
| PATCH | `/workspaces/{id}/members/{memberId}` | `{role}` → `Member` | **M**. 409 `last_manager`. |
| DELETE | `/workspaces/{id}/members/{memberId}?reassignTo={memberId}` | → 204 | **M**. Soft delete (`status=removed`). Their pending tasks move to `reassignTo`; without it, a member with pending tasks gets 409 `member_has_open_tasks`. Completed tasks keep their assignee. 409 `last_manager`. |

Status meanings:
- `pending`: invited by username, hasn't started the bot.
- `active`
- `unreachable`: blocked the bot. DMs fail, so show a warning.
- `removed`

### Invites

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/invites?state=` | → `{items: Invite[]}` | **M**. Default `state=active`. `state` is `active`, `expired`, `revoked` or `used_up`. |
| POST | `/workspaces/{id}/invites` | `InviteCreate {expiresInHours=168 (1–720), maxUses=1 (null = unlimited)}` → `Invite` 201 | **M**. `url` = `https://t.me/<botUsername>?start=<token>`. 409 `bot_not_connected`. The dashboard renders the QR code from `url`. |
| DELETE | `/workspaces/{id}/invites/{inviteId}` | → 204 | **M**. Revokes the invite; idempotent. |

Redeeming an invite happens in the bot (`/start <token>`, B2) through the shared `redeem_invite(token, telegram_user)` service, and emits `member.joined`.

### Projects

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/projects?status=active\|archived\|all` | → `{items: Project[]}` | **M**. Default `active`. Each has `taskCounts {total, pending, completed, overdue}`. Sorted by name. |
| POST | `/workspaces/{id}/projects` | `{name, description?}` → `Project` 201 | **M** |
| GET | `/workspaces/{id}/projects/{projectId}` | → `Project` | **M** |
| PATCH | `/workspaces/{id}/projects/{projectId}` | `{name?, description?}` → `Project` | **M** |
| POST | `/workspaces/{id}/projects/{projectId}/archive` | → `Project` | **M**, idempotent. Its tasks remain; pending ones still remind. |
| POST | `/workspaces/{id}/projects/{projectId}/unarchive` | → `Project` | **M**, idempotent |
| DELETE | `/workspaces/{id}/projects/{projectId}` | → 204 | **M**. Only when it has no tasks: 409 `project_not_empty`. |

### Tasks

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/tasks` | → `Page<Task>` | **M**. Filters: `projectId*`, `assigneeId*`, `status=pending\|completed`, `overdue=true`, `dueAfter` (inclusive), `dueBefore` (exclusive), `q` (title/description search), `includeArchived=false`. `sort`: `dueAt` (default), `-dueAt`, `createdAt`, `-createdAt`, `number`, `-number`. `page`, `pageSize`. |
| POST | `/workspaces/{id}/tasks` | `TaskCreate {title, description?, projectId, assigneeId, dueAt, reminderOffsetsMinutes?}` → `Task` 201 | **M**. Queues the assignment DM in the same transaction. 422 `project_archived` or `assignee_not_active`. |
| GET | `/workspaces/{id}/tasks/{taskId}` | → `Task` | **M** |
| PATCH | `/workspaces/{id}/tasks/{taskId}` | `TaskUpdate` (all optional) → `Task` | **M**. Changing `assigneeId` reassigns: the old DM is marked no longer yours, and the new assignee gets a DM. Changing `dueAt` reschedules reminders and tells the assignee. Changing `reminderOffsetsMinutes` replaces the per-task offsets, and `[]` falls back to the workspace default. |
| POST | `/workspaces/{id}/tasks/{taskId}/complete` | → `Task` | **M**, idempotent. Sets `completedVia=dashboard`, and the assignee's DM is updated. |
| POST | `/workspaces/{id}/tasks/{taskId}/reopen` | → `Task` | **M**, idempotent. Back to `pending`, and reminders are rescheduled. |
| DELETE | `/workspaces/{id}/tasks/{taskId}` | → 204 | **M**. Hard delete. The assignee's DM is edited to say the task was cancelled. |
| GET | `/workspaces/{id}/tasks/{taskId}/activity` | → `{items: ActivityItem[]}` | **M**. Oldest first. A merged timeline of `task_events` (created, reassigned, completed via Telegram, …) and Telegram deliveries (assigned or reminder sent or failed). For the C4 task detail. |

`Task` fields:
- `id`, `number`, `title`, `description`
- `project {id, name}`
- `assignee {id, displayName, telegramUsername, status}`
- `dueAt`, `status` (`pending`/`completed`), and `isOverdue` (pending and `dueAt` < now)
- `completedAt`, `completedVia` (`telegram`/`dashboard`/`system`), `completedBy`, `createdBy`
- `reminderOffsetsMinutes`: the effective offsets
- `delivery {state: queued|sent|failed|unreachable|not_linked, sentAt, lastError}`: the assignment DM to the current assignee
- `createdAt`, `updatedAt`

There are only two statuses. The old NEW/IN_PROGRESS/DONE mapping is gone. dnd-kit columns are Pending and Completed.

### Analytics

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/workspaces/{id}/analytics?from=YYYY-MM-DD&to=YYYY-MM-DD` | `Analytics` | **M**. Default range is the last 30 days ending today, in the workspace timezone. |

`Analytics` fields:
- `range {from, to}`
- `totals {total, completed, pending, overdue, completionRate}`
- `byProject[]` (the same fields plus `projectId`, `name`)
- `byMember[]` (the same fields plus `memberId`, `displayName`, `openTasks`)
- `overdueTasks[]` (`Task`, oldest first, at most 20)
- `generatedAt`

Semantics:
- **In range** means `dueAt` falls on a local date between `from` and `to`, inclusive.
- `total`, `completed`, `pending` and `overdue` count tasks in range.
- `completionRate = completed / total` (0–1), or `null` when `total = 0`.
- `overdue` = pending and `dueAt` < now.
- `openTasks` (workload) and `overdueTasks` ignore the range: they show *now*.
- Tasks in archived projects are included.
- Target: under 200 ms on seed data (A6).

## 6. Realtime (Socket.IO)

- **Endpoint:** the API host, path `/socket.io`, namespace **`/v1`**.

```ts
import { io } from "socket.io-client";
const socket = io(`${API_BASE}/v1`, {
  path: "/socket.io",
  transports: ["websocket"],
  auth: (cb) => authClient.token().then((r) => cb({ token: r.data?.token })), // fresh JWT on every (re)connect
});
socket.on("connect_error", (err) => { /* err.message === "unauthenticated" → refresh session or go to login */ });
socket.on("task.completed", (e: TaskEventPayload) => queryClient.invalidateQueries(...));
```

**Connect.** The server verifies `auth.token` exactly as in REST. If it's invalid, the connection is refused with `connect_error` message `unauthenticated`. Otherwise the socket joins room `ws:{workspaceId}` for every workspace where the caller is an active manager. Joining is done by the server, never by the client.
- Creating a workspace adds it to the caller's open sockets.
- Being removed or demoted takes the user out of that workspace's room.
- Tokens are checked only at connect, and a reconnect fetches a fresh token through `auth`.

**Delivery.** Events are at-most-once, with no replay. After every (re)connect, the client should refetch or invalidate everything it shows. Treat events as cache invalidation hints that also carry the new state, which makes optimistic UI easy.

**Envelope.** Every event has `{event, workspaceId, at, actor: {source: telegram|dashboard|system, memberId|null}}` plus:

| Event | Extra payload | Emitted when |
|---|---|---|
| `task.created` | `task: Task` | Task created |
| `task.updated` | `task: Task, changed: string[]` | Edit, reassign or due change (`changed` lists camelCase field names) |
| `task.completed` | `task: Task` | Completed, from Telegram or the dashboard |
| `task.reopened` | `task: Task` | Reopened |
| `task.deleted` | `taskId, projectId` | Deleted |
| `task.delivery` | `taskId, delivery: Delivery` | Assignment DM sent or failed, or member unreachable |
| `project.created` / `project.updated` | `project: Project` | Created, edited, archived or unarchived |
| `project.deleted` | `projectId` | Deleted |
| `member.joined` | `member: Member` | Invite redeemed, or a pending member matched on `/start` |
| `member.updated` | `member: Member` | Role or status changed (e.g. `unreachable`) |
| `member.removed` | `memberId` | Removed |
| `bot.updated` | `bot: Bot` | Connected, disconnected or marked invalid |
| `workspace.updated` | `workspace: Workspace` | Settings changed |

TypeScript types for every payload are in `openapi.json` under `components.schemas` (`TaskEventPayload`, `TaskDeletedPayload`, `TaskDeliveryPayload`, `ProjectEventPayload`, `ProjectDeletedPayload`, `MemberEventPayload`, `MemberRemovedPayload`, `BotEventPayload`, `WorkspaceEventPayload`).

The legacy `statusChanged` event and the open, unauthenticated root namespace go away in A2.

## 7. TypeScript types (Stream C)

```bash
# in nest-dashboard
npm i -D openapi-typescript openapi-fetch
npx openapi-typescript ../telegram-task-bot/docs/openapi.json -o lib/api/schema.d.ts
# or from a running API: npx openapi-typescript http://localhost:8001/openapi.json -o lib/api/schema.d.ts
```

```ts
import createClient from "openapi-fetch";
import type { paths, components } from "./schema";
export type Task = components["schemas"]["Task"];
export const api = createClient<paths>({ baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL });
api.use({ async onRequest({ request }) { request.headers.set("Authorization", `Bearer ${await getToken()}`); return request; } });
const { data, error } = await api.GET("/api/v1/workspaces/{workspaceId}/tasks", {
  params: { path: { workspaceId }, query: { status: "pending", pageSize: 100 } },
});
```

Regenerate the types whenever `docs/openapi.json` changes. Its git log is the changelog.

## 8. Legacy endpoint mapping

These are removed in A1/A4 once the dashboard has switched over.

| Legacy (`bot.py`) | v1 |
|---|---|
| `POST /api/auth/login` (mock) | removed; use the better-auth JWT |
| `GET /api/users`, `GET /api/members` | `GET /workspaces/{id}/members` |
| `POST /api/members/invite` | `POST /workspaces/{id}/invites` (link) or `POST /workspaces/{id}/members` (@username) |
| `GET/POST /api/projects`, `PATCH/DELETE /api/projects/{id}` | `/workspaces/{id}/projects[...]` (+ `archive`/`unarchive`) |
| `GET/POST /api/tasks`, `PATCH/DELETE /api/tasks/{id}` | `/workspaces/{id}/tasks[...]` (+ `complete`/`reopen`) |
| `GET /api/analytics` | `GET /workspaces/{id}/analytics` |
| `PATCH /api/workspaces/{id}/bot-token` | `PUT /workspaces/{id}/bot` |
| `POST /webhook` | `POST /telegram/{botId}` (B1) |
| socket `statusChanged` | `task.completed` / `task.reopened` in namespace `/v1` |
| `localStorage.workspaceId`, `ws_01` | `workspaceId` in the URL (`/w/[workspaceId]/...`), from `GET /me` |

## 9. Open points

- **Manager Telegram linking** (B2, optional): the manager would get a deep link that attaches their Telegram account to their own `members` row, so they receive completion DMs. The endpoint shape will be agreed with Stream B before B2. It probably needs a link-token column (a small migration from Stream A).
- **Workspace deletion and data export** (D4): not in v1 of this contract yet.
- **Member-role dashboard access:** v2.
