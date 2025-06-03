# --------- Base stage ---------
FROM python:3.12-slim AS base

WORKDIR /app

RUN groupadd -r ddns && \
    useradd -r -g ddns -s /bin/false ddns

# --------- Dependencies stage ---------
FROM python:3.12-slim AS dependencies

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# --------- Final stage ---------
FROM base AS final

WORKDIR /app

COPY --from=dependencies /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY src/ .

RUN chown -R ddns:ddns /app

USER ddns

# Environment variables
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python"]

CMD ["main.py"]

LABEL description="Cloudflare DDNS Updater" \
      version="2.1.0"
