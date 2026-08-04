# AssetAtlas Backend - Thin App Image
# Contains only application code on top of the base image
# Used for both the web API and the scheduler worker
# by specifying different commands in docker-compose
#
# BASE_IMAGE defaults to the offline path (localhost/...). CI release builds
# pass BASE_IMAGE=ghcr.io/<owner>/assetatlas-base@sha256:<digest> as a build-arg
# — a digest, not a tag, so a warm-but-stale local `:latest` can never be
# substituted for the base that release.yml's staleness guard actually verified.

ARG BASE_IMAGE=localhost/assetatlas-base:latest
FROM ${BASE_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/dotdna-io/assetatlas" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ARG APP_VERSION=0.0.0-dev
ARG BUILD_DATE=unknown
ARG GIT_COMMIT=unknown
ENV APP_VERSION=${APP_VERSION}
ENV BUILD_DATE=${BUILD_DATE}
ENV GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

# Switch to root to copy files with correct ownership
USER root

# Copy application code
COPY --chown=assetatlas:assetatlas src/ ./src/

# Copy device knowledge base (overrides base image version with latest patterns)
COPY --chown=assetatlas:assetatlas data/device_knowledge_base/ ./data/device_knowledge_base/

# The COPY above overwrites real files from the base image, so a pointer here is
# strictly worse than nothing. product_lines.csv shipped as a 130-byte git-LFS
# pointer for months (#1092): a checkout without `lfs: true` yields pointers, and
# the only symptom was a "Bad product line row" warning at boot while product-line
# detection quietly under-resolved. .gitattributes now keeps that file out of LFS;
# this gate is what turns a regression into a red build instead of a silent one.
RUN for f in data/device_knowledge_base/*.csv; do \
        if head -c 50 "$f" | grep -q 'git-lfs.github.com'; then \
            echo "ERROR: $f is a git-LFS pointer, not content (run 'git lfs pull')" >&2; \
            exit 1; \
        fi; \
    done

# Copy Prometheus alert rules. sync_prometheus_rules() reads these at boot to
# create the alert_rules rows that the Alertmanager webhook resolves incoming
# alerts against (#1093) — without them every firing alert is accepted and
# dropped as unknown_alertname.
COPY --chown=assetatlas:assetatlas monitoring/alerts/ ./monitoring/alerts/

# Copy static files (synonym lookup for search)
COPY --chown=assetatlas:assetatlas static/ ./static/

# Copy logo files (needed by /api/integrations/logos endpoint)
COPY --chown=assetatlas:assetatlas ui/prod/public/logos/ ./ui/prod/public/logos/

# Gunicorn hooks for Prometheus multiprocess mode (#1128). Only the api runs
# gunicorn, and only the api passes `-c`, so this file is inert in the other
# services built from this same image.
#
# PROMETHEUS_MULTIPROC_DIR is deliberately NOT set here: api, scheduler and mcp
# are ONE image under three names, and an ENV would switch the single-process
# scheduler into multiprocess mode too — where its start_http_server serves the
# default registry, which holds no mmap values, silently emptying
# assetatlas_scheduler_last_heartbeat_seconds and with it the
# system-scheduler-stalled rule. It is set per-service in compose instead.
COPY --chown=assetatlas:assetatlas gunicorn.conf.py ./gunicorn.conf.py

USER assetatlas

# Default: API server via gunicorn.
#
# This is the authoritative api command — compose deliberately does NOT override
# it. `-c` and the file it names must ship together: compose and the image do
# not arrive as one unit (an online install applies the bundle's compose but
# pulls `:latest`), so a `-c` pinned in compose against an image without the
# file makes gunicorn abort and the api never start.
#
# The access/error log flags were previously only in the compose override and
# are folded in here so removing it changes nothing about logging.
CMD ["gunicorn", "-c", "/app/gunicorn.conf.py", \
     "--bind", "0.0.0.0:5001", \
     "--workers", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "src.api.factory:create_app()"]
