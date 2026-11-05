#!/bin/sh
set -e

# Find the database (and wait for it to be ready), then hand the working URL
# to migrations and the bot. Logs go to stderr; only the URL is captured.
DATABASE_URL="$(python -m bot.db.wait)"
export DATABASE_URL

alembic upgrade head
exec python -m bot.main
