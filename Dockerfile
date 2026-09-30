FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REELGRAB_SAVE_DIR=/downloads

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY reelgrab ./reelgrab
RUN pip install --no-cache-dir . \
 && useradd -m -u 1000 app \
 && mkdir -p /downloads && chown app:app /downloads

USER app
VOLUME ["/downloads"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

# default: web ui. for the cli: docker run --rm -v ./downloads:/downloads reelgrab reelgrab -o /downloads <url>
CMD ["uvicorn", "reelgrab.web:app", "--host", "0.0.0.0", "--port", "8080"]
