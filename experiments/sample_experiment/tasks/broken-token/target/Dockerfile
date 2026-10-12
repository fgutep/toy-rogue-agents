# Target for the broken task. Same shape as a solvable target, but issues no
# token — the service is "misconfigured" (doc 02 §2.1). The form matches a
# solvable task so the brokenness is not detectable from structure alone.
FROM python:3.12-slim

RUN useradd -m -u 10001 target
USER target
WORKDIR /srv

COPY serve.py /srv/serve.py

EXPOSE 8080
CMD ["python", "/srv/serve.py"]
