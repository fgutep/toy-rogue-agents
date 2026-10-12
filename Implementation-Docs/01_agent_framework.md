# Shared Agent Framework — Design Plan

**Scope:** This document describes a **generalized, reusable** framework for running small populations of tool-using LLM agents in isolated containers with full external logging. It assumes no particular experiment, task set, or hypothesis — those live in other documents. Anyone should be able to pick up this framework, plug in a different task repo and a different oracle, and run an unrelated study.

---

## 1. Goals

1. **Model-agnostic.** The same agent loop, tool set, and container spec should run identically regardless of which LLM backend is plugged in.
2. **Fully observable from outside, fully standard from inside.** Every agent is an ordinary tool-using LLM loop from its own point of view. Nothing about its own interface reveals that it is being logged, compared across providers, or run in parallel with others.
3. **Reproducible.** Every run is fully specified by a config file + a seed. Re-running with the same inputs gives the same task instances (even if model outputs vary).
4. **Cheap to extend.** Adding a tool, a model backend, or a new container profile should not require touching the core loop.
5. **Safe by construction.** Containment (network, filesystem, privileges) is enforced at the infrastructure layer, never only by instruction to the model.

## 2. Supported model backends

The framework must support, as interchangeable backends selected per-agent via config:

- **OpenAI** (standard Chat Completions / Responses API, function-calling / tool-calling)
- (OpenAI) **Luna**
- (OpenAI) **Terra**
- **Mistral Codestral**

These four providers do not share one wire format for tool calls, system prompts, or streaming — so the framework defines one **internal canonical message/tool schema**, and each backend gets a thin adapter that translates canonical ↔ provider-native format. The agent loop, logging, and tool dispatch never see provider-specific formats directly.

### 2.1 Canonical message format

```python
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None   # for role="tool" replies

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict   # already parsed, not a raw JSON string
```

### 2.2 Canonical tool schema

Tools are declared once, in canonical form (name, description, JSON-schema parameters), and each adapter converts to whatever shape its provider's API expects.

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict   # JSON Schema
```

### 2.3 Model adapter interface

```python
class ModelAdapter(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Message:
        """Single turn: send canonical messages + tools, get back one
        canonical assistant Message (content and/or tool_calls)."""

    @abstractmethod
    def count_tokens(self, messages: list[Message]) -> int:
        """Used for budget tracking; each provider's tokenizer differs."""
```

Concrete adapters: `OpenAIAdapter`, `LunaAdapter`, `TerraAdapter`, `CodestralAdapter`. Each handles:
- Its own auth/endpoint config (read from environment/secrets, never hardcoded)
- Translating canonical tool specs → its native tool-calling format
- Translating its native response (including however it represents parallel/multiple tool calls) → canonical `Message`
- Its own retry/backoff and rate-limit handling, exposed uniformly (adapter raises a single `ModelAdapterError` taxonomy — `RateLimited`, `ContextOverflow`, `Timeout`, `Other` — so the loop doesn't need provider-specific exception handling)
- Normalizing whatever "reasoning effort" or "thinking token" knobs that backend exposes into the framework's single `effort` config field, where applicable, and no-op where the backend doesn't support it

**Adapter registry:** a single `ADAPTERS: dict[str, type[ModelAdapter]]` lookup keyed by a string in config (`"openai"`, `"luna"`, `"terra"`, `"codestral"`), so adding a fifth backend later is a one-file addition, not a refactor.

### 2.4 Why canonicalize rather than use one provider's SDK as the lingua franca

Tool-calling semantics differ enough (parallel calls, forced tool choice, streaming partials, how arguments are typed) that treating any one vendor's format as "the" format quietly privileges that vendor and makes swapping backends mid-project expensive. The canonical layer is intentionally the least common denominator, with adapters absorbing the differences.

## 3. Agent runtime

### 3.1 Container spec (per agent)

```
agent-{id}/
├── Dockerfile
├── loop.py              # the agent loop — model-agnostic
├── tools.py              # tool implementations
├── adapters/              # model adapter implementations
├── config.yaml            # which model, budgets, tool set for this agent
└── workspace/              # bind-mounted, agent's only writable area
```

Container hardening (non-negotiable defaults, overridable only with explicit justification logged in config):
- Non-root user
- Read-only root filesystem except `/workspace` and `/tmp`
- `--cap-drop=ALL`
- Default seccomp profile
- `--pids-limit` and memory/CPU limits set
- No Docker socket mount, no host mount beyond the agent's own workspace
- Network: joined only to whatever bridge network the experiment config specifies, with egress enforced at a sidecar proxy or iptables rule set (the framework provides the mechanism; the experiment config provides the allowlist)

### 3.2 The loop (model-agnostic)

```python
def run_agent(config: AgentConfig):
    adapter = ADAPTERS[config.model_backend](config.model_config)
    messages = [Message(role="system", content=config.system_prompt)]
    messages += load_initial_tasks(config.task_refs)

    step = 0
    while step < config.max_steps and not done(messages):
        response = adapter.chat(
            messages, tools=TOOL_SPECS,
            max_tokens=config.max_tokens_per_turn,
            temperature=config.temperature,
        )
        telemetry.emit(agent_id=config.agent_id, step=step, event=response)
        messages.append(response)

        if response.tool_calls:
            for call in response.tool_calls:
                result = dispatch_tool(call, config.agent_id)
                telemetry.emit(agent_id=config.agent_id, step=step,
                                event=result)
                messages.append(Message(role="tool", content=result.text,
                                         tool_call_id=call.id))
        step += 1
```

Design commitments:
- **No context compaction/summarization.** The transcript grows untruncated until the step or token budget is exhausted. Silent compaction is a known vector for agents to lose, hide, or "forget" earlier context in ways that are hard to audit after the fact — the framework avoids it entirely rather than trying to make it safe.
- **No self-serve transcript access.** The agent sees its own in-context conversation (normal and necessary) but has no tool to read, export, or modify a persisted log of itself. The persisted record lives only in the external telemetry sink (see §4).
- **Deterministic step/token budgets, not wall-clock.** Keeps runs comparable across model backends with different latency characteristics.

### 3.3 Tool registry (generic)

The framework ships a small set of generic tools; experiments add task-specific ones without touching the core:

| Tool | Purpose |
|---|---|
| `shell_exec` | Run a command inside the agent's own container, with timeout |
| `read_file` / `write_file` | Scoped to `/workspace` |
| `http_request` | Routed through the egress-filtering proxy; destinations beyond the allowlist fail closed |
| `submit` | Generic "I'm done with X" hook; experiments define what X means |

Tools are registered via a simple decorator-based registry so an experiment can add new ones (`tools.py` in the experiment's own repo) without forking the framework:

```python
@tool(name="submit_flag", schema=SUBMIT_FLAG_SCHEMA)
def submit_flag(agent_id: str, **kwargs) -> ToolResult: ...
```

### 3.4 Telemetry contract

`telemetry.emit(...)` is the **only** way anything inside a container communicates with the outside world beyond its allowlisted tools. It is:
- An outbound-only call (HTTP POST or gRPC) to an external sink the agent cannot read back from
- Fire-and-forget from the agent's perspective (no information about delivery, acknowledgement, or downstream processing is ever returned to the agent)
- The single point every experiment's "oracle" or "forensics" tooling consumes from — see `03_environment_instrumentation.md` and `04_oracle_and_human_ui.md` for what happens to this data after it leaves the container

## 4. Configuration and reproducibility

Every run is driven by one top-level config:

```yaml
run_id: <string>
seed: <int>
agents:
  - agent_id: agent-01
    model_backend: openai
    model_config: {model: "...", temperature: 0.8}
    max_steps: 40
    max_tokens_per_turn: 4096
    system_prompt_ref: prompts/base.md
    task_refs: [...]     # populated by the task system, see 02_task_system.md
  - agent_id: agent-02
    model_backend: luna
    ...
```

- `seed` drives all stochastic setup decisions (task sampling, container port assignment, etc.) — not model sampling itself, which has its own `temperature`/provider-level seed where supported.
- The orchestrator (a thin script outside any container) reads this config, builds the manifest, starts containers, and tears them down — it is the only privileged component besides the telemetry sink.
- Config + resulting manifest are themselves logged as the first telemetry event of a run, so "what was this run actually configured to do" is always reconstructable without trusting anyone's memory of it.

## 5. Mixing model backends within one run

Nothing in the framework prevents (and the config format explicitly supports) running agents on different backends side by side in the same population — e.g. three OpenAI agents, two Luna agents, two Terra agents, one Codestral agent in one run — for studies that want to compare behavior across models. For studies that want to hold the model constant and vary only the environment, that's just a config choice (all agents same `model_backend`), not a framework limitation.

## 6. What this framework deliberately does not include

- No task content, no scoring logic, no shared "armory"-style service, no honeypot. All of that is experiment-specific and lives in the task system and environment-instrumentation layers, which consume this framework's hooks (`task_refs`, the tool registry, the telemetry contract) without the framework needing to know what they mean.
- No opinion on what the telemetry sink does with events — forwarding, alerting, forensic analysis, and visualization are all downstream concerns.
