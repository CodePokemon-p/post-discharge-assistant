FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first: Docker re-uses this step unless requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code (see .dockerignore for what is left out).
COPY . .

# Never run as root inside the container.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Hosts such as Render tell the app which port to use through $PORT.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"

CMD ["sh", "-c", "uvicorn webhook:app --host 0.0.0.0 --port ${PORT:-8000}"]