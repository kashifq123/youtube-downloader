FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

ENV PORT=7860
EXPOSE 7860
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-7860} --workers 1 --threads 8 --timeout 600
