#!/bin/sh
# Start MangaVibe. When the container runs as root (Synology default), switch to
# PUID:PGID if that user can read the library; otherwise stay root and say why.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
PORT="${PORT:-8090}"
CMD="uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --no-access-log"

if [ "$(id -u)" != "0" ] || [ "$PUID" = "0" ]; then
  exec $CMD
fi

mkdir -p /data
FIRST_LIB=$(echo "${LIBRARY_PATHS:-/manga}" | cut -d, -f1)
if ! setpriv --reuid="$PUID" --regid="$PGID" --clear-groups ls "$FIRST_LIB" >/dev/null 2>&1; then
  echo "[mangavibe] User $PUID:$PGID cannot read $FIRST_LIB, so MangaVibe keeps running as root."
  echo "[mangavibe] That is fine on Synology (the library is mounted read-only). To run as your own"
  echo "[mangavibe] user instead, set PUID/PGID to your DSM user's id (see docs/SYNOLOGY.md)."
  exec $CMD
fi

if [ "$(stat -c %u /data)" != "$PUID" ]; then
  echo "[mangavibe] Giving /data to user $PUID:$PGID"
  chown -R "$PUID:$PGID" /data || echo "[mangavibe] Warning: could not change owner of /data"
fi
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups $CMD
