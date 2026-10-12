"""agentlab — a generalized, contained laboratory for tool-using LLM agents.

The package is layered to match the design documents in ``Implementation-Docs/``:

* :mod:`agentlab.schema`      — canonical message / tool schema (doc 01 §2)
* :mod:`agentlab.adapters`    — pluggable model backends (doc 01 §2.3)
* :mod:`agentlab.loop`        — the model-agnostic agent loop (doc 01 §3.2)
* :mod:`agentlab.tools`       — decorator tool registry + generic tools (doc 01 §3.3)
* :mod:`agentlab.telemetry`   — the one-way telemetry contract (doc 01 §3.4)
* :mod:`agentlab.tasks`       — task repo, instantiation, manifest (doc 02)
* :mod:`agentlab.eventstore`  — unified envelope + append-only store (doc 03 §3)
* :mod:`agentlab.armory`      — the shared cache under study (doc 03 §1)
* :mod:`agentlab.honeypot`    — the honeypot target (doc 03)
* :mod:`agentlab.oracle`      — cheap grader + perceived-complexity layer (doc 04 §1)
* :mod:`agentlab.ui`          — host-only read-only operator dashboard (doc 04 §2)
* :mod:`agentlab.orchestrator`— the single privileged driver (doc 01 §4)

Nothing in this package decides *what* an experiment means; experiments plug in
through ``task_refs``, the tool registry, and the telemetry contract.
"""

__version__ = "0.1.0"
