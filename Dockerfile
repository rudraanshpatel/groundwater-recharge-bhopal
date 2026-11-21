# Web app image: FastAPI backend + Leaflet frontend + the committed processed data.
#
#   docker build -t gwr .
#   docker run -p 7860:7860 gwr      ->  http://localhost:7860
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=7860

# rasterio's bundled GDAL needs libexpat, which the slim image leaves out
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user
RUN useradd -m -u 1000 app
WORKDIR /app

COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt

COPY --chown=app:app backend/ backend/
COPY --chown=app:app frontend/ frontend/
COPY --chown=app:app data/ data/

USER app
EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT}"]
