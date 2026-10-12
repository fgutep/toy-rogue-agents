# Minimal target service for the find-token task (doc 02 §2).
#
# Serves the instance's token at GET /token. The orchestrator passes the
# per-instance flag in via the FLAG build arg / env when it builds and launches
# this target (doc 02 §3). In local (no-Docker) runs the token is instead seeded
# directly into the agent workspace by the LocalRunner; this image is the
# containerized equivalent for real runs on the Ubuntu host.
FROM python:3.12-slim

ARG FLAG="FLAG{placeholder}"
ENV FLAG=${FLAG}

RUN useradd -m -u 10001 target
USER target
WORKDIR /srv

COPY serve.py /srv/serve.py

EXPOSE 8080
CMD ["python", "/srv/serve.py"]
