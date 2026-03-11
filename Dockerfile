FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY g3lobster ./g3lobster
COPY config.yaml ./config.yaml
COPY config ./config

RUN pip install --no-cache-dir .

# Cloud Run injects PORT (default 8080); the app reads it at startup.
ENV PORT=8080
EXPOSE ${PORT}

# Use shell form so $PORT is expanded at runtime.
CMD python -m g3lobster --port $PORT
