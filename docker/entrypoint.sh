#!/bin/sh
# Start MangaVibe. When the container runs as root (Synology default), switch to
# PUID:PGID if that user can read the library; otherwise stay root and say why.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
PORT="${PORT:-8090}"
DATA="${DATA_DIR:-/data}"

# Auto-update (app/updater.py): run the newest downloaded version from $DATA/code/<sha>
# instead of the code built into the image. A version that fails to start 3 times is
# replaced by the one before it (or the built-in code), and remembered in $DATA/code/failed.
APP_DIR=/app
CODE="$DATA/code"
# A downloaded version belongs to the image it was installed on. After pulling a new image, run the
# image's own code: it is at least as new, and the old download may not work with the new image.
IMAGE_SHA=$(cat /opt/mangavibe/image/sha 2>/dev/null || true)
if [ -n "$IMAGE_SHA" ] && [ -f "$CODE/current" ]; then
  SHA=$(cat "$CODE/current")
  if [ "$(cat "$CODE/$SHA/.image" 2>/dev/null || true)" != "$IMAGE_SHA" ]; then
    echo "[mangavibe] New Docker image, so its built-in code is used instead of downloaded version $SHA"
    rm -f "$CODE/current"
  fi
fi
if [ "${AUTO_UPDATE:-1}" != "0" ] && [ -f "$CODE/current" ]; then
  SHA=$(cat "$CODE/current")
  DIR="$CODE/$SHA"
  if [ -n "$SHA" ] && [ -f "$DIR/app/main.py" ] && [ ! -f "$DIR/.ok" ]; then
    TRIES=$(cat "$DIR/.tries" 2>/dev/null || echo 0)
    if [ "$TRIES" -ge 3 ]; then
      echo "[mangavibe] Version $SHA did not start 3 times, so it is not used. See the log above for the error."
      echo "$SHA" > "$CODE/failed"
      PREV=$(cat "$CODE/previous" 2>/dev/null || true)
      if [ -n "$PREV" ] && [ "$PREV" != "$SHA" ] && [ -f "$CODE/$PREV/.ok" ]; then
        echo "$PREV" > "$CODE/current"
        DIR="$CODE/$PREV"
      else
        rm -f "$CODE/current"
        DIR=""
      fi
    else
      echo $((TRIES + 1)) > "$DIR/.tries"
    fi
  fi
  if [ -n "$DIR" ] && [ -f "$DIR/app/main.py" ]; then
    APP_DIR="$DIR"
    echo "[mangavibe] Running version $(basename "$DIR")"
  fi
fi
if [ -d "$APP_DIR/deps" ]; then
  export PYTHONPATH="$APP_DIR/deps"
fi
cd "$APP_DIR"
CMD="uvicorn app.main:app --app-dir $APP_DIR --host 0.0.0.0 --port $PORT --proxy-headers --no-access-log"

if [ "$(id -u)" != "0" ] || [ "$PUID" = "0" ]; then
  exec $CMD
fi

mkdir -p "$DATA"
FIRST_LIB=$(echo "${LIBRARY_PATHS:-/manga}" | cut -d, -f1)
if ! setpriv --reuid="$PUID" --regid="$PGID" --clear-groups ls "$FIRST_LIB" >/dev/null 2>&1; then
  echo "[mangavibe] User $PUID:$PGID cannot read $FIRST_LIB, so MangaVibe keeps running as root."
  echo "[mangavibe] That is fine on Synology (the library is mounted read-only). To run as your own"
  echo "[mangavibe] user instead, set PUID/PGID to your DSM user's id."
  exec $CMD
fi

if [ "$(stat -c %u "$DATA")" != "$PUID" ]; then
  echo "[mangavibe] Giving $DATA to user $PUID:$PGID"
  chown -R "$PUID:$PGID" "$DATA" || echo "[mangavibe] Warning: could not change owner of $DATA"
fi
exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups $CMD
