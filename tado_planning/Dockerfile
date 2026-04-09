FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask requests "python-tado>=0.18"

COPY tado_planning/tado-planning-run.py /tado-planning-run.py
COPY tado_planning/tado-planning-cfg.py /tado-planning-cfg.py
COPY tado_planning/run.sh               /run.sh
COPY tado_planning/static/              /static/
COPY tado_planning/templates/           /templates/
COPY tado_planning/config.json          /config.json.addon
COPY tado_planning/schedules.tmpl/      /schedules.tmpl/

RUN chmod +x /run.sh

CMD ["/run.sh", "--loop"]
