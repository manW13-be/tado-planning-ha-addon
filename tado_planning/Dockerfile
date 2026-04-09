FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask requests "python-tado>=0.18"

COPY tado-planning-run.py /tado-planning-run.py
COPY tado-planning-cfg.py /tado-planning-cfg.py
COPY run.sh               /run.sh
COPY static/              /static/
COPY templates/           /templates/
COPY config.json          /config.json.addon
COPY schedules.tmpl/      /schedules.tmpl/

RUN chmod +x /run.sh

CMD ["/run.sh", "--loop"]
