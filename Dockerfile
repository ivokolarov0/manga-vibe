FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TZ=UTC PORT=8090 DATA_DIR=/data LIBRARY_PATHS=/manga PUID=1000 PGID=1000 CACHE_MAX_GB=2 \
    MALLOC_ARENA_MAX=2

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

# 7-Zip reads .7z/.cb7, .cbr/.rar and ZIPs that Python can't (Deflate64/PPMd). It comes from the
# 7-Zip project (static 7zzs build, checksum pinned), not Debian: Debian stable ships fixes late,
# so scanners keep flagging its 7zip package. unrar is left out: scanners flag it for a WinRAR CVE.
# To update: new version and the sha256 of both tar.xz files from github.com/ip7z/7zip/releases.
ARG TARGETARCH
ARG SEVENZIP_VERSION=2606
RUN case "$TARGETARCH" in \
      amd64) arch=x64;   sha=2b539cf067b2ad48e1baa5093dbaa8818b73542d1222a0ffde9dc5f2922ea62e ;; \
      arm64) arch=arm64; sha=edcc408aacce77da5223728f2eceb5e9f6dc713b8a9df934ad21e2a7f7560627 ;; \
      *) echo "no 7-Zip build for $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && python -c "import hashlib, io, sys, tarfile, urllib.request; \
data = urllib.request.urlopen(sys.argv[1], timeout=60).read(); \
hashlib.sha256(data).hexdigest() == sys.argv[2] or sys.exit('7-Zip download: wrong checksum'); \
tarfile.open(fileobj=io.BytesIO(data)).extract('7zzs', '/usr/local/bin', filter='data')" \
      "https://github.com/ip7z/7zip/releases/download/${SEVENZIP_VERSION%??}.${SEVENZIP_VERSION#??}/7z${SEVENZIP_VERSION}-linux-${arch}.tar.xz" "$sha" \
 && mv /usr/local/bin/7zzs /usr/local/bin/7zz \
 && chmod 755 /usr/local/bin/7zz \
 && 7zz i | grep -q Rar5

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
