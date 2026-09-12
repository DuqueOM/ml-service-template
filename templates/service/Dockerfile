# =============================================================================
# Multi-stage Dockerfile for {@ service_name @}
# =============================================================================
# Image size: the runtime/training split took the installed set from 150
# resolved packages to 35 (ADR-049). Re-measure with `docker images` rather
# than trusting a number in a comment.
#
# Stage 1 (builder): Install system build tools + Python packages in a venv
# Stage 2 (runtime): Copy only the venv + app code into a clean slim image
#
# Key decisions (see ADR):
#   - Multi-stage reduces final image ~40% vs single-stage
#   - xgboost/lightgbm installed with --no-deps to avoid nvidia-nccl-cu12 (~290MB)
#   - Non-root USER for K8s Pod Security Standards compliance
#   - HEALTHCHECK for Docker Compose; K8s uses its own probes
#   - Single uvicorn worker — K8s HPA manages horizontal scaling
#     NEVER use --workers N in K8s (causes CPU thrashing, dilutes HPA signal)
#   - Model artifacts are NOT baked in — K8s init container downloads them
#
#   - requirements.txt is the RUNTIME set only. Training (mlflow, optuna) and
#     test tooling (pytest, locust, ...) are deliberately absent: nothing that
#     runs in this image imports them, and shipping them put 20 of this
#     service's 24 fixable CRITICAL/HIGH advisories into every inference pod
#     (ADR-049). Train with `pip install -r requirements-train.txt` outside
#     the served image.
#
# TODO: Update LABEL metadata for your service.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — install dependencies in an isolated venv
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS builder

LABEL maintainer="your-team@example.com" \
      version="1.0.0" \
      description="{@ service_name @} — ML prediction API"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# System build deps (required for scikit-learn, scipy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ build-essential \
    && rm -rf /var/lib/apt/lists/*

# Runtime requirements only — see the header. Copying requirements-train.txt
# or requirements-dev.txt here would silently undo the partition, so don't.
COPY requirements.txt ./

# Install into a venv for clean multi-stage copy
# xgboost/lightgbm --no-deps avoids pulling nvidia-nccl-cu12 (~290MB)
#
# `set -e` here is load-bearing, not boilerplate: without it, a failing
# pip install partway through this chain does NOT fail the RUN as long as
# some later command in the script still exits 0 — Docker reports the
# layer as built even though the venv is missing dependencies, and the
# break only surfaces later, at container runtime, as a confusing
# "executable not found" / ModuleNotFoundError (see ADR / anti-patterns:
# a masked `; true` sentinel at the end of this exact RUN was the original
# form of this bug). The cleanup find/rm commands below are safe under
# set -e because they only ever operate with -f/glob semantics that don't
# fail on a missing path. `pip check` runs right after install (BEFORE
# cleanup removes pip's own site-packages, which would break `pip check`
# itself) as a cheap, dependency-agnostic sanity net that catches an
# inconsistent/partial venv without needing to hardcode which packages
# this service actually installs.
RUN set -e && \
    python -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --no-compile -r requirements.txt && \
    # Uncomment if using xgboost/lightgbm:
    # pip install --no-cache-dir --no-deps "xgboost>=1.5.0,<3.0" && \
    # pip install --no-cache-dir --no-deps "lightgbm>=4.0.0,<5.0" && \
    # narwhals: required transitive dep of lightgbm>=4.7, excluded by --no-deps
    # (pip check below catches this if omitted):
    # pip install --no-cache-dir "narwhals>=1.15" && \
    pip check && \
    # Clean up to reduce image size
    find /opt/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null; \
    find /opt/venv -type d -name 'tests' -not -path '*/numpy/*' -exec rm -rf {} + 2>/dev/null; \
    find /opt/venv -type f -name '*.pyc' -delete 2>/dev/null; \
    rm -rf /opt/venv/lib/python*/site-packages/pip 2>/dev/null; \
    rm -rf /opt/venv/lib/python*/site-packages/setuptools 2>/dev/null

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal final image
# ---------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/app/src \
    PATH="/opt/venv/bin:$PATH" \
    MODEL_PATH=models/model.joblib

# Minimal runtime deps (curl for healthcheck, libgomp for lightgbm/xgboost),
# plus the distro's pending security patches.
#
# `apt-get upgrade` is here because the base image lags Debian's security
# archive, and the served image's Trivy gate (CRITICAL,HIGH, --ignore-unfixed)
# holds the image to what is FIXABLE, not to what the base happens to ship.
# Measured: three days after that gate first went green, two fixable HIGHs
# appeared in a package neither this Dockerfile nor requirements.txt names —
#
#   libpcre2-8-0  CVE-2026-86145  out-of-bounds write, arbitrary code
#   libpcre2-8-0  CVE-2026-89161  memory corruption
#   installed 10.42-1, fixed in 10.42-1+deb12u1
#
# — and the gate blocked the build, which is the gate working rather than
# breaking. Waiting for the upstream base tag to rebuild is not a control; it
# is a hope with a release cadence attached.
#
# The trade-off, stated: this makes the OS layer non-reproducible across
# builds. It already was — `apt-get install` without pinned versions resolves
# against a moving archive — so `upgrade` changes the security posture without
# changing that property. Reproducibility at this layer belongs to a digest-
# pinned base image, which is a separate decision (D-17..D-19 pin what we
# publish, not what Debian publishes).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libgomp1 \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Drop the system pip and setuptools. The venv at /opt/venv already has them
# removed (see the builder's cleanup) and is first on PATH, so nothing in this
# image can reach these — they are attack surface with no function.
#
# They are also the image's only fixable CRITICAL/HIGH findings. Measured
# against python:3.13-slim-bookworm:
#
#   CVE-2025-47273       setuptools 70.3.0 -> needs 78.1.1   HIGH
#   GHSA-6v7p-g79w-8964  msgpack 1.1.2 -> needs 1.2.1        HIGH   (vendored inside pip)
#
# Removing beats upgrading here: the serving image installs nothing at runtime,
# so a *current* setuptools would be the same unused surface with a later CVE.
# Verified before removal that every entrypoint this image runs imports cleanly
# with pip, setuptools and pkg_resources blocked from sys.meta_path.
#
# The golden path now scans this image with the same Trivy gate the generated
# service ships (CRITICAL,HIGH, --ignore-unfixed), so a regression here fails
# the build rather than shipping quietly.
RUN rm -rf /usr/local/lib/python*/site-packages/pip \
           /usr/local/lib/python*/site-packages/pip-*.dist-info \
           /usr/local/lib/python*/site-packages/setuptools \
           /usr/local/lib/python*/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python*/site-packages/pkg_resources \
           /usr/local/lib/python*/site-packages/wheel \
           /usr/local/lib/python*/site-packages/wheel-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

# Non-root user — required by K8s Pod Security Standards (baseline)
RUN groupadd -r appuser --gid=1000 && \
    useradd -r -g appuser --uid=1000 --home-dir=/app appuser

WORKDIR /app

# Copy venv from builder
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

# Copy configs first (changes less frequently → better Docker cache)
COPY --chown=appuser:appuser configs/ ./configs/

# Copy source code (changes more frequently)
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser monitoring/ ./monitoring/

# Closed-loop monitoring helpers (D-21, ADR-006). Without this, the service
# starts but the PredictionLogger silently degrades — production thinks it has
# closed-loop monitoring while no events are written. The lifespan now
# fails fast when PREDICTION_LOG_ENABLED=true AND common_utils is missing.
COPY --chown=appuser:appuser common_utils/ ./common_utils/

# Build-time smoke import (Phase 1.1 / audit closure):
# Catches a missing/renamed module — or a runtime dependency that the
# requirements split dropped by mistake — at *image build* time instead of
# letting the runtime image ship and crash on first boot.
#
# The list is every entrypoint this image actually runs, which is what makes
# it the executable proof that requirements.txt is sufficient:
#   - app.main:            the uvicorn CMD below
#   - training.features:   app/fastapi_app.py imports FeatureEngineer at
#                          startup and RAISES unless FEATURE_ENGINEERING_
#                          REQUIRED=false, so serving genuinely depends on
#                          this one module under training/
#   - monitoring.*:        the drift + performance CronJobs run these from
#                          THIS image (k8s/base/cronjob-*.yaml)
#   - common_utils.*:      hard runtime deps — prediction_logger (D-21),
#                          input_validation (PR-R2-4), secrets (D-17/D-18)
# If any import fails, `docker build` exits non-zero and the image never
# reaches the registry — the regression cannot reach production.
#
# What is deliberately NOT importable here: training.train, training.
# promote_to_mlflow and cli, the three modules that import mlflow/optuna.
# They run on a CI runner or a training job with requirements-train.txt
# installed, never in the served image.
RUN /opt/venv/bin/python -c "import app.main, common_utils.prediction_logger, common_utils.input_validation, common_utils.secrets" \
    && /opt/venv/bin/python -c "import {@ service_slug @}.training.features, {@ service_slug @}.monitoring.drift_detection, {@ service_slug @}.monitoring.performance_monitor" \
    && echo "runtime import smoke OK"

# Create runtime directories (models downloaded by K8s init container)
RUN mkdir -p logs data/raw data/processed data/reference models results && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Docker HEALTHCHECK (K8s uses its own liveness/readiness probes)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Single worker — NEVER use --workers N in K8s
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
