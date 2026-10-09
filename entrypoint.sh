#!/bin/sh
set -e
echo "Applying migrations..."
until python manage.py migrate --noinput; do echo "Database not ready, retrying in 3s..."; sleep 3; done
python manage.py collectstatic --noinput
if [ "$SEED_DEMO" = "true" ]; then python manage.py seed_demo || true; fi
exec "$@"
