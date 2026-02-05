# ==========================================
# STAGE 1: Builder
# ==========================================
FROM python:3.11-slim-bookworm as builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# 1. Install build tools (gcc) AND dependency headers (unixodbc-dev)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    unixodbc-dev \
    libc-dev \
    libffi-dev && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ==========================================
# STAGE 1: Runtime
# ==========================================
FROM python:3.11-alpine as runtime

WORKDIR /app

# 3. Install RUNTIME libraries only (no gcc needed here)
# - unixodbc: for pyodbc execution
# - ffmpeg: for pydub audio processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    unixodbc \
    ffmpeg \
    groupadd \
    libpq5 && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . .

# (Optional) Download textblob corpora if you haven't done it in code
# RUN python -m textblob.download_corpora

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 3000
# The standard production formula for Gunicorn workers is number of cores + 1
CMD ["gunicorn", \
     "--bind", "0.0.0.0:3000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--log-level", "info", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "run:app"]
