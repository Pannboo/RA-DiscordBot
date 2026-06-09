# RA-DiscordBot

A multi-guild Discord bot that brings RetroAchievements into Discord:
live unlock feeds, mastery / beaten / completion awards, session start
notifications, points milestones, profile lookups, server leaderboards,
head-to-head comparisons, per-game progress cards, and a rotating
"Achievement of the Week" bot presence.

- **Modern UX** — slash commands, ephemeral menus, modals, buttons,
  channel/user select dropdowns, paginated profile tabs.
- **Multi-guild** — server admins set one announce channel per server;
  users get posted in every server they share with the bot.
- **Adaptive polling** — 30s while a linked user is actively playing
  (Discord presence *or* recent RA activity), every minute or so otherwise.
- **Hardcore + softcore** — both are posted, distinguished by color and icon
  (users can opt into hardcore-only via the settings menu).
- **One shared RA API key** — your key, used to read public data for every
  user you've linked. RA API hits are minimised: one weekly call for AOTW,
  one cached console list at startup, batched polls per due user.
- **No pings** — embeds post quietly; the bot never `@`-mentions anyone.

## Commands

### User commands

| Command | Where | Description |
| --- | --- | --- |
| `/ra hub` | Anywhere | Ephemeral control panel: Link / Profile / Settings / Refresh / Unlink. |
| `/ra link` | Anywhere | Ephemeral modal to link your RA username (validated against the RA API). |
| `/ra unlink` | Anywhere | Removes the link. |
| `/ra status` | Anywhere | Show your link status / last poll / cached totals. |
| `/ra profile [username]` | Anywhere | Ephemeral RA profile lookup with Overview / Recent / Awards tabs (defaults to your own). |
| `/ra game <id or url>` | Anywhere | Game lookup: set info, console link, and your progress (hardcore + softcore bars). Accepts a numeric game id or a full `retroachievements.org/game/<id>` URL. |
| `/ra top [sort]` | Guild | Top-10 linked members in the server. Sort by hardcore points, softcore, RetroPoints, or masteries. |
| `/ra vs @member [other]` | Guild | Head-to-head comparison: hardcore, softcore, RetroPoints, masteries, global rank. |

### Admin commands (require *Manage Server*)

| Command | Description |
| --- | --- |
| `/ra admin hub` | Admin control panel: native channel-picker dropdown + pause/resume toggle + per-user Manage button. |
| `/ra admin setchannel #channel` | Set this server's announce channel. |
| `/ra admin disable` | Stop posting in this server (keeps the channel configured). |
| `/ra admin status` | Show this server's configuration. |

The **Manage user** button in `/ra admin hub` opens a `UserSelect` dropdown
that lets an admin link or unlink a member's RA account on their behalf
(best-effort DM is sent to the target so they know).

## What gets posted

All events go to the guild's configured announce channel as embed-only
messages (no pings, ever).

- **Unlock** — every achievement unlock. Hardcore = warm gold + 🔥,
  softcore = silver + 💧. Shows points, RetroPoints (hardcore only),
  badge image, game, and console (linked to the RA system page).
- **Now Playing** — fires once when a user starts a new game session
  (detected from RA's `LastGame` field). Silent for the very first poll
  on a fresh link.
- **Award** — mastery (HC), completion (SC), beaten (HC/SC). Big cover-art
  image, flavour text, console linked.
- **Points Milestone** — every time a user crosses 1k / 5k / 10k / 25k /
  50k / 100k / … hardcore, softcore, or RetroPoints.

The very first time the bot sees a freshly-linked user it **silently seeds**
unlocks, awards, sessions, and totals from the RA API — no flood of
historical events.

## Bot presence

The bot's own Discord status rotates every 30 seconds between the
current **Achievement of the Week** and its **Game**, using custom
activities (no `Watching` / `Playing` prefix) so neither line gets
truncated in the member list. RA is hit exactly **once per week** —
right after the new AOTW rolls over (computed from RA's `StartAt`).

## Setup

### 1. Discord application

1. Create an application + bot at <https://discord.com/developers/applications>.
2. Under **Bot → Privileged Gateway Intents**, enable:
   - **Presence Intent** (active-tier detection)
   - **Server Members Intent** (multi-guild dispatch)
3. Copy the bot token into `DISCORD_TOKEN`.
4. Invite the bot with scopes `bot` + `applications.commands` and the
   permissions: *View Channel*, *Send Messages*, *Embed Links*.

### 2. RetroAchievements API key

Generate a key at <https://retroachievements.org/controlpanel.php>
("Web API Keys"). Then set:

```
RA_API_USERNAME=YourRAUsername
RA_API_KEY=...
```

### 3. Environment

```bash
cp .env.example .env
# fill in DISCORD_TOKEN, RA_API_USERNAME, RA_API_KEY
# DEV_GUILD_IDS is optional but instant-syncs slash commands during development
```

### 4. Run with Docker (recommended)

```bash
docker compose up -d --build
```

Postgres comes up alongside the bot; migrations run on startup.

### 5. Run locally

For quick local testing, SQLite is the path of least resistance —
just leave the default `DATABASE_URL` in `.env.example` alone.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
alembic revision --autogenerate -m "initial"   # first time only
alembic upgrade head
python -m bot.main
```

For production use Postgres:

```
DATABASE_URL=postgresql+asyncpg://ra:ra@host:5432/ra_bot
```

## Useful maintenance scripts

### Reseed (factory-reset event history)

If event-tracking state ever gets stuck or you want a clean baseline,
run the reseed script. It wipes all the seen-tables and re-fetches the
current RA state silently so only events *after this point* will fire:

```bash
.venv/bin/python -m scripts.reseed
```

## How polling works

Two cooperating loops drive event detection:

- **Tick loop** (every `POLL_TICK_SECONDS`, default 10s) selects users
  whose `next_poll_at` has elapsed and calls `GetUserRecentAchievements`
  + `GetUserSummary` for each. New unlocks become embeds, game-id
  changes become "Now Playing" embeds, threshold crossings become
  milestone embeds.
- **Awards loop** (every `AWARDS_INTERVAL_SECONDS`, default 15 min)
  calls `GetUserAwards` and emits mastery / completion / beaten
  embeds. First-ever poll for a user is seeded silently so the channel
  doesn't fill with a year of history when they link.

After each poll the user is reclassified:

- **Active (30s)** if Discord presence shows them in a "Playing X"
  activity in the last hour, *or* they earned an RA unlock in the last
  hour, *or* their RA `LastGame` changed (i.e. they started a new game).
- **Idle (cadence from `POLL_IDLE_SECONDS`)** otherwise.

Presence updates flip a user into the active tier the instant their
game starts — no waiting for the next idle tick.

## Schema notes

All event types are deduplicated in the database
(`unlocks_seen`, `awards_seen`, `milestones_seen`, `game_sessions`) so
restarts never re-post the same event.

## Repo layout

```
bot/
  main.py              # entrypoint
  config.py            # env-driven settings
  commands.py          # /ra slash command tree (hub, link, profile, game, top, vs, admin)
  aotw.py              # rotating Achievement-of-the-Week bot presence
  db/                  # SQLAlchemy 2.0 async models + session + UTCDateTime
  ra/                  # RA Web API client, pydantic response models, console cache
  embeds/              # discord.Embed renderers (unlock / session / award / milestone / game / leaderboard / vs)
  poller/
    scheduler.py       # tick + awards loops
    activity.py        # presence listener
    detector.py        # diff RA state -> events
    dispatcher.py      # fan out events to every guild the user is a member of
  ui/                  # ephemeral views (hub, admin hub, profile tabs, settings, admin-user manage)
alembic/               # migrations
scripts/
  reseed.py            # factory-reset event history and re-seed silently
```
