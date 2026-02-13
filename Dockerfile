FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY g3lobster ./g3lobster
COPY config.yaml ./config.yaml
COPY config ./config

RUN pip install --no-cache-dir .

EXPOSE 20001

CMD ["python", "-m", "g3lobster"]
