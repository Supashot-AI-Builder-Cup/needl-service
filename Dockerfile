# needl-service for Cloud Run. One uvicorn process: the job queue lives in memory.
# The legacy endpoints are not supported here (no Chromium); deploy with ENABLE_LEGACY_ROUTES=false.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UPLOAD_DIR=/tmp/needl-uploads

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .
COPY app/ app/

# Render mounts secret files (the Firestore key) at /etc/secrets, readable by group 1000 only.
RUN (getent group 1000 >/dev/null || groupadd --gid 1000 secrets) \
    && useradd --uid 10001 --gid 1000 --no-create-home --shell /usr/sbin/nologin needl
USER 10001

EXPOSE 8080
# Cloud Run sends SIGTERM and waits 10 s; stop accepting work after 8 s. Cloud Run logs requests itself.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-graceful-shutdown 8 --no-access-log"]
