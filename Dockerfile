FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-calc-nogui fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config
COPY templates ./templates

RUN useradd --create-home --uid 10001 estimator && mkdir -p /srv/data && chown -R estimator /srv/data
USER estimator

ENV DATA_DIR=/srv/data \
    CONFIG_DIR=/srv/config \
    PYTHONUNBUFFERED=1 \
    HOME=/home/estimator

VOLUME ["/srv/data"]
CMD ["python", "-m", "app.slack_app"]
