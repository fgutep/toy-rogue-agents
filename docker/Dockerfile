# One image for every agentlab process (services + agents); the command differs
# per container. Built with uv for fast, locked installs (doc: use uv for Python).
FROM python:3.12-slim

# uv, pinned by copying from its official image.
COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md LICENSE ./
COPY agentlab ./agentlab
RUN uv pip install --system --no-cache .

# Non-root by default; containers that need a specific uid override `user:`.
RUN useradd -m -u 10001 appuser
USER 10001:10001

# Default command is a no-op; compose sets each service's command explicitly.
CMD ["python", "-c", "print('set a command in compose')"]
