ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# 1. Install System Dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    unixodbc \
    unixodbc-dev \
    libc-dev \
    libffi-dev \
    ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# 2. Setup Venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 3. Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. Copy the actual application code
COPY . .

# 5. Permission Setup
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 3000

CMD ["gunicorn", \
     "--bind", "0.0.0.0:3000", \
     "--workers", "3", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--log-level", "info", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "run:app"]
