FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask requests "python-tado>=0.18"

COPY cfg/tado-planning-cfg.py /tado-planning-cfg.py
COPY cfg/run.sh                /run.sh
COPY cfg/static/               /static/
COPY cfg/templates/            /templates/

RUN chmod +x /run.sh

CMD ["/run.sh"]
