FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app/src
# Railway injects PORT at runtime; no hardcoded EXPOSE needed.
# For local Docker usage the default is 9000 (see start_server.py).
CMD ["python", "scripts/start_server.py"]
