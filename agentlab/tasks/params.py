"""Deterministic per-instance parameter sampling (design doc 02 §3).

Instantiating a template produces a ``params`` dict sampled from
``params.schema.yaml`` using the run's seed *and* the instance tag, so:

* the same template handed to two agents yields two different flags (one
  agent's answer is not directly copyable by another), and
* re-running with the same seed + tag reproduces the exact same params
  (reproducibility; doc 01 §1).

Randomness is derived from a SHA-256 of ``(seed, task_id, instance_tag,
param_name)`` so each param is independent and the whole thing is pure — no
reliance on global RNG state or ordering.

``params.schema.yaml`` maps a param name to a spec:

```yaml
flag:        {type: flag, prefix: "FLAG{", length: 16}
port_offset: {type: int,  min: 0, max: 500}
secret_word: {type: choice, choices: [alpha, bravo, charlie]}
nonce:       {type: hex,  length: 8}
```
"""

from __future__ import annotations

import hashlib
import random
import string
from pathlib import Path
from typing import Any

import yaml

_FLAG_ALPHABET = string.ascii_lowercase + string.digits


def _rng_for(seed: int, task_id: str, instance_tag: str, param: str) -> random.Random:
    key = f"{seed}|{task_id}|{instance_tag}|{param}".encode()
    digest = hashlib.sha256(key).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _sample_one(spec: dict[str, Any], rng: random.Random) -> Any:
    kind = spec.get("type")
    if kind == "flag":
        prefix = spec.get("prefix", "FLAG{")
        suffix = spec.get("suffix", "}")
        length = int(spec.get("length", 16))
        body = "".join(rng.choice(_FLAG_ALPHABET) for _ in range(length))
        return f"{prefix}{body}{suffix}"
    if kind == "hex":
        length = int(spec.get("length", 8))
        return "".join(rng.choice("0123456789abcdef") for _ in range(length))
    if kind == "int":
        lo, hi = int(spec.get("min", 0)), int(spec.get("max", 100))
        return rng.randint(lo, hi)
    if kind == "choice":
        return rng.choice(list(spec["choices"]))
    if kind == "const":
        return spec["value"]
    raise ValueError(f"unknown param type {kind!r} in params.schema.yaml")


def sample_params(
    schema_path: str | Path, *, seed: int, task_id: str, instance_tag: str,
) -> dict[str, Any]:
    """Sample a fresh ``params`` dict for one instance, deterministically."""
    path = Path(schema_path)
    if not path.exists():
        return {}
    schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    params: dict[str, Any] = {}
    for name, spec in schema.items():
        rng = _rng_for(seed, task_id, instance_tag, name)
        params[name] = _sample_one(spec, rng)
    return params
