FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TZ=UTC PORT=8090 DATA_DIR=/data LIBRARY_PATHS=/manga PUID=1000 PGID=1000 CACHE_MAX_GB=2 \
    MALLOC_ARENA_MAX=2

# 7zip reads .7z/.cb7 and ZIPs that Python can't (Deflate64/PPMd). With 7zip-rar (Debian's
# non-free section) it also reads .cbr/.rar. unrar is left out: scanners flag it for a WinRAR CVE.
RUN sed -i 's/^Components: main$/Components: main non-free/' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends 7zip 7zip-rar tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Packages are installed with uv, and pip is removed: pip bundles its own copies of urllib3,
# msgpack and setuptools that security scanners flag. The updater uses uv at runtime too.
RUN pip install --no-cache-dir uv==0.13.5 \
 && uv pip install --system --no-cache -r requirements.txt \
 && pip uninstall -y pip

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
