FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TZ=UTC PORT=8090 DATA_DIR=/data LIBRARY_PATHS=/manga PUID=1000 PGID=1000 CACHE_MAX_GB=2 \
    MALLOC_ARENA_MAX=2

# unrar (.cbr/.rar) and 7zip-rar live in Debian's non-free section. 7zip reads .7z/.cb7 and
# ZIPs that Python can't (Deflate64/PPMd); with 7zip-rar it is also the backup RAR reader.
RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends unrar 7zip 7zip-rar tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY examples ./examples
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Auto-update (app/updater.py) compares a new version's Dockerfile, entrypoint and requirements
# with these copies: a changed Dockerfile or entrypoint needs a new image, new pip packages don't.
ARG GIT_SHA=""
COPY Dockerfile requirements.txt /opt/mangavibe/image/
RUN cp /entrypoint.sh /opt/mangavibe/image/entrypoint.sh && echo "$GIT_SHA" > /opt/mangavibe/image/sha

EXPOSE 8090
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8090'), timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
