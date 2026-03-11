FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY g3lobster ./g3lobster
COPY config.yaml ./config.yaml
COPY config ./config

RUN pip install --no-cache-dir .

# Install Node.js and the Gemini CLI so GeminiAgent subprocess spawning works.
# The slim base image has no Node runtime, so we install via NodeSource.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g gemini-cli && \
    apt-get purge -y curl && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Cloud Run injects PORT (default 8080); the app reads it at startup.
ENV PORT=8080
EXPOSE ${PORT}

# Use shell form so $PORT is expanded at runtime.
CMD python -m g3lobster --port $PORT
