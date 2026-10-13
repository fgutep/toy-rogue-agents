from __future__ import annotations

import os
import time

import httpx

from agentlab.telemetry import Event


def main() -> None:
    path = os.environ["NOTE_PATH"]
    content = os.environ["NOTE_CONTENT"]
    headers = {
        "authorization": f"Bearer {os.environ['ARMORY_TOKEN']}",
        "x-agent-identity": "orchestrator",
    }
    response = None
    for _ in range(30):
        try:
            response = httpx.put(
                f"http://armory:8800/cache/{path}",
                headers=headers,
                content=content,
                timeout=2,
            )
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    if response is None or response.status_code != 200:
        raise RuntimeError("Armory did not accept the planted note")

    event = Event(
        source="orchestrator",
        kind="planted_note",
        payload={"path": path, "body_sha256": response.text},
    )
    result = httpx.post(
        "http://telemetry-sink:8900/events",
        content=event.model_dump_json(exclude_none=True),
        headers={"content-type": "application/json"},
        timeout=2,
    )
    result.raise_for_status()


if __name__ == "__main__":
    main()
