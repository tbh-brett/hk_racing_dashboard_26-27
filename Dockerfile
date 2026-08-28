# The dashboard, its scheduler and its replication in one image.
#
# WHY ONE CONTAINER AND NOT THREE. The database is a single SQLite file on a
# Fly volume, and a Fly volume attaches to exactly one machine. Anything that
# writes to it — the scraper, the derive pass — must run on that machine. So
# the split that would be natural elsewhere (web here, cron there) is not
# available, and pretending otherwise would mean a second machine that cannot
# reach the data it exists to update.
#
# Three processes, in a deliberate order:
#   litestream  supervises everything, so replication is running before the
#               first write and flushes after the last one
#   supercronic runs the scrape and derive schedule
#   uvicorn     serves the dashboard

FROM python:3.11-slim

# ── binaries ─────────────────────────────────────────────────────────────────
# Both are single static binaries pinned by version and checked by SHA. Not
# apt packages: the Debian archive has neither, and an unpinned GitHub release
# URL is a silent upgrade waiting for a deploy.
#
# supercronic rather than Debian cron, for one specific reason: cron runs jobs
# with an EMPTY environment. HKRD_DB would be unset, the scrape would write to
# ./hkrd.db inside the container instead of /data/hkrd.db on the volume, and
# every count would look right while the dashboard read a different file that
# never changed. supercronic inherits the container's environment and logs to
# stdout, so `fly logs` shows the scrape. (The crontab passes --db explicitly
# as well: this failure is bad enough to guard twice.)
ARG LITESTREAM_VERSION=0.3.13
ARG SUPERCRONIC_VERSION=0.2.29

# Measured against the published release assets, both architectures. A release
# asset can be replaced under an unchanged tag; without these a rebuild months
# from now could pull a different binary and nothing would say so.
ARG LITESTREAM_SHA256_amd64=eb75a3de5cab03875cdae9f5f539e6aedadd66607003d9b1e7a9077948818ba0
ARG LITESTREAM_SHA256_arm64=9585f5a508516bd66af2b2376bab4de256a5ef8e2b73ec760559e679628f2d59
ARG SUPERCRONIC_SHA256_amd64=87625cd179eff21226f0be6f2f47dd357037064598e6c1f9ffcbd0335d402bbd
ARG SUPERCRONIC_SHA256_arm64=063799a43c1eac082d83ac59a43a6896b50d69aa1f533c2cc6a5376cb2bfff89

# Set by BuildKit. Defaulted so a plain `docker build` still works.
ARG TARGETARCH=amd64

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl tzdata; \
    \
    case "${TARGETARCH}" in \
      amd64) ls_sha="$LITESTREAM_SHA256_amd64"; sc_sha="$SUPERCRONIC_SHA256_amd64" ;; \
      arm64) ls_sha="$LITESTREAM_SHA256_arm64"; sc_sha="$SUPERCRONIC_SHA256_arm64" ;; \
      *) echo "no checksum recorded for arch ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    \
    curl -fsSL -o /tmp/litestream.tar.gz \
      "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    echo "$ls_sha  /tmp/litestream.tar.gz" | sha256sum -c -; \
    tar -C /usr/local/bin -xzf /tmp/litestream.tar.gz litestream; \
    \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "$sc_sha  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic; \
    \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /tmp/*

# Race times, meeting dates and "did tonight's scrape run" are all Hong Kong
# facts. The container thinks in HKT so a crontab line means what it reads as.
ENV TZ=Asia/Hong_Kong
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# ── the application ──────────────────────────────────────────────────────────
WORKDIR /app

# Dependencies before source, so editing a query module does not rebuild scipy.
# The first install exists only to populate that layer — pyproject alone is not
# installable, so the package skeleton comes with it.
COPY pyproject.toml README.md ./
COPY hkrd/__init__.py hkrd/__init__.py
RUN pip install --no-cache-dir .

COPY hkrd/ hkrd/
COPY web/ web/
COPY ops/ ops/

# --force-reinstall, and NOT editable. The first install left a one-file `hkrd`
# in site-packages; an editable install on top of it adds /app to the path
# without removing that, and which one wins depends on path order. Two copies
# of a package where one is a stub is a fault that surfaces as an ImportError
# for a module that is plainly on disk. Reinstalling outright leaves one copy.
RUN pip install --no-cache-dir --no-deps --force-reinstall . \
    && chmod +x ops/entrypoint.sh

# LITESTREAM_ENDPOINT and _REGION are defaulted empty rather than left unset:
# Litestream expands ${...} in its config file, and an undefined variable there
# is a startup error, not an empty string. Empty means "AWS S3", which is the
# correct default for the one provider that needs no endpoint.
ENV PYTHONUNBUFFERED=1 \
    HKRD_DB=/data/hkrd.db \
    HKRD_HOST=0.0.0.0 \
    HKRD_PORT=8080 \
    LITESTREAM_ENDPOINT="" \
    LITESTREAM_REGION=""

EXPOSE 8080
ENTRYPOINT ["/app/ops/entrypoint.sh"]
