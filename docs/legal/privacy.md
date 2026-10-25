> **DRAFT. Not legal advice and not ready to publish.**
> Zak, Bark and a lawyer need to review it first. Written 2026-10-25 from what the v1 code actually stores and sends. Anything in `[brackets]` needs a decision. Lines starting with **Reviewer note:** are for the reviewers and come out before publishing. Stream C turns the final text into the `/privacy` page.

# Nest Privacy Policy

*Last updated: [DATE]*

Nest is a task management service. Managers plan work in a web dashboard, and team members receive and complete tasks through Telegram. Nest is operated by **[LEGAL ENTITY NAME]** ("we", "us"), [REGISTERED ADDRESS], Addis Ababa, Ethiopia. Contact: **[PRIVACY EMAIL]**.

This policy explains what we collect, why, where it is kept, and the choices you have.

## 1. Who is responsible for your data

- **Managers' accounts.** We decide how dashboard account data (name, email, sign-in details) is used, so we are responsible for it.
- **Workspace content.** A workspace belongs to the organisation that created it (the "Customer"). Projects, tasks, the member list, and the Telegram bot the Customer connects are processed **on the Customer's behalf and following their instructions**. If you are a team member, your organisation decides what goes into its workspace. Send questions about that content to them first. We will help them answer you.

**Reviewer note:** confirm the controller/processor split and whether a separate Data Processing Agreement is needed for business customers.

## 2. What we collect

### Managers (dashboard users)
- **Account:** name, email address, profile picture (if you sign in with Google), and a hashed password (if you sign up with email). We never see your Google password.
- **Sign-in sessions:** session records, which include the IP address and browser type used to sign in, so we can keep your account secure.
- **Workspace settings:** workspace name, time zone, and reminder preferences.

### Team members (Telegram users)
When you open an invite link and start a workspace's bot, Telegram tells us:
- your **Telegram user ID**, **username**, **first and last name**, and **language setting**;
- the ID of your **private chat** with that bot, so the bot can send you tasks.

We do **not** get your phone number, contacts, or messages from other chats.

### Work data
- **Projects and tasks:** titles, descriptions, due dates, who each task is assigned to, and its status (pending or completed).
- **Activity history:** for example, "created", "reassigned", or "completed via Telegram", with who did it and when.
- **Messages we send:** a copy of each task notification the bot sent you (its text), and whether it was delivered.
- **Invite links:** when they were created, how often they were used, and whether they were revoked.

### The Customer's Telegram bot
The bot token the Customer pastes in from Telegram's BotFather. We store it **encrypted** and use it only to run that workspace's bot.

### Technical data
- **Server logs:** which address called which endpoint, when, and whether it worked. Bot tokens are automatically removed from logs.
- **Error reports:** when something breaks, we send the error and technical context to our error-monitoring provider (Sentry). It is configured not to collect personal data by default, and we strip tokens and credentials before anything is sent.

## 3. What we do *not* do

- **We don't read your group chats.** Nest's bots only respond in private chats with people who started them, and they ignore group messages completely.
- **We don't sell data**, and we don't use it for advertising.
- **We don't use AI on your content** in this version of Nest.
- **We don't track you across other websites.**

**Reviewer note:** if product analytics (e.g. PostHog, plan item D3) is added before launch, this section and section 6 must change.

## 4. Why we use it (legal bases)

| Purpose | Data | Basis |
|---|---|---|
| Provide the service: create workspaces, deliver tasks and reminders by Telegram, show progress in the dashboard | Account, member, work and bot data | Performing our contract with the Customer and with you |
| Keep accounts and the service secure: sign-in sessions, rate limiting, abuse prevention | Sessions, IP addresses, logs | Our legitimate interest in a safe, reliable service |
| Fix problems | Logs and error reports | Legitimate interest |
| Meet legal obligations | Whatever the law requires | Legal obligation |

**Reviewer note:** check the bases against Ethiopia's Personal Data Protection Proclamation (No. 1321/2024), and against the GDPR if we serve people in the EU (our hosting is in the EU).

## 5. Where your data is stored

- Our database is hosted by **Neon** in the **AWS eu-central-1 (Frankfurt, Germany)** region.
- Our servers run on **Render** in **Frankfurt, Germany**.
- Error reports go to **Sentry** ([REGION: confirm EU or US data residency]).
- Messages to and from Telegram go through **Telegram's** own servers, under Telegram's privacy policy.

This means data from Ethiopia and elsewhere is transferred to and stored in Germany (EU). [Describe the transfer safeguards once the lawyer confirms them.]

## 6. Who we share data with

Only service providers that help us run Nest, and only what each one needs:

| Provider | What for | Data |
|---|---|---|
| Neon | Database hosting | All stored data |
| Render | Application hosting | Data passing through our servers, logs |
| Sentry | Error monitoring | Error details and technical context |
| Telegram | Delivering bot messages | Messages the bot sends you, your Telegram ID |
| Google | Sign in with Google (if you use it) | Your Google account email, name and picture, received by us |
| [Email provider, e.g. Resend] | Password reset and verification emails | Your email address |

We share data with authorities only when the law requires it.

## 7. How long we keep it

- **Workspace data** (projects, tasks, members, activity, message history) is kept for as long as the workspace exists. A manager can **export** the whole workspace as a file, or **delete** it permanently, from the workspace settings. Deleting removes its projects, tasks, members, invites, activity and stored bot token.
- **Telegram profile details** (ID, username, name) can be shared by several workspaces you belong to, so they are kept until you are no longer in any workspace. [Implementation gap: we don't purge these automatically yet. Decide a retention period.]
- **Removed members** stay in the workspace's history, marked "removed", so past tasks still show who did them. The workspace can be deleted to erase them.
- **Manager accounts** are kept until you ask us to delete your account. [Implementation gap: there's no self-service account deletion yet.]
- **Backups:** our database provider keeps point-in-time backups for [N] days, so deleted data disappears from backups after that.
- **Logs and error reports** are kept for [N] days by our hosting and monitoring providers.

## 8. Your rights

Depending on where you live, you may have the right to **access**, **correct**, **delete** or **export** your data, to **object** to or **restrict** some uses, and to **complain** to a data protection authority.

- **Team members:** ask your workspace manager, or contact us. You can stop receiving messages at any time by blocking the bot in Telegram.
- **Managers:** contact us at [PRIVACY EMAIL]. You can export or delete your workspace yourself from Settings.

We answer within [30] days.

## 9. Security

- Bot tokens are encrypted at rest, and passwords are stored hashed.
- All traffic uses HTTPS.
- Every workspace's data is kept separate and checked at the database level.
- Access to the dashboard API requires a signed, short-lived token.
- We limit request rates to block abuse.

No system is perfectly secure. If a breach affects your data, we will tell you and the authorities as the law requires.

## 10. Children

Nest is a workplace tool and is not meant for anyone under [16 / 18].

## 11. Changes

When we change this policy, we update the date at the top. For significant changes, we tell managers by email or in the dashboard before the change takes effect.

## 12. Contact

[LEGAL ENTITY NAME], [ADDRESS]. [PRIVACY EMAIL].
