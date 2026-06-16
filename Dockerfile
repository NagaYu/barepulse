# BarePulse — production image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code.
COPY app ./app

# Run as a non-root user.
RUN useradd --create-home --uid 10001 barepulse \
    && chown -R barepulse:barepulse /app
USER barepulse

EXPOSE 8000

# Honor $PORT if the platform injects one (Render, Fly, …); default 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
