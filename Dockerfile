FROM python:3.11-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "python-tado>=0.18"
COPY tado_planning.py /tado_planning.py
COPY run.sh /run.sh
COPY schedules/ /default_schedules/
RUN chmod +x /run.sh
CMD ["/run.sh"]