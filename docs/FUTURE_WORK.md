# Future work

## Capture model reasoning traces (GPT Luna / reasoning backends)

**Goal:** record each agent's reasoning, not just its tool calls, so the
forensics/influence analysis can see *why* an agent acted (e.g. whether it
believed a planted note), instead of inferring from action sequences.

**What we learned (probe, 2026-10-13):**
- The `luna` credential reaches `api.openai.com` and exposes reasoning
  models, including **`gpt-5.6-luna`** (also `gpt-5.6-sol`, `gpt-5.x-pro`, …).
- Reasoning traces are **not** available through the Chat Completions API the
  current adapter uses (`chat.completions.create`). They come from the
  **Responses API** (`client.responses.create`).
- A probe of `gpt-5.6-luna` with `reasoning={"effort":"low","summary":"auto"}`
  returned `output` items `["reasoning", "message"]`; the `reasoning` item
  carried a human-readable **summary** (a worked-through explanation), and the
  `message` item the final answer. So reasoning *summaries* are exposed and
  capturable (raw CoT is not, by design).
- Per the API docs, `reasoning.mode` (standard|pro) and `reasoning.effort`
  (default medium) are independent knobs.

**Blockers / work needed (none of this exists yet):**
1. **Schema** — `agentlab/schema.py::Message` has no reasoning field. Add an
   optional `reasoning: str | None = None`.
2. **Loop** — `agentlab/loop.py` logs a `model_turn` with only
   `content` + `tool_calls`; reasoning would be discarded. Add `reasoning`
   to the `model_turn` payload so it lands in the event store and timeline.
3. **Adapter** — `_openai_compat` / `luna` must call the **Responses API**
   (not Chat Completions) for reasoning models, translate tools to the
   Responses tool shape, extract the reasoning-summary item into
   `Message.reasoning`, and pass `reasoning={mode, effort}`. The framework
   already has an `effort` knob (`AgentConfig.effort` →
   `_effort_kwargs` → `reasoning_effort`) but it targets Chat Completions;
   it needs to map onto the Responses `reasoning` object instead.
4. **Config** — a scenario/run using `model_backend: luna` with
   `model_config: {model: gpt-5.6-luna, ...}` and an `effort`/`mode` setting.
5. **Forensics/UI** — optionally surface the captured reasoning in the
   timeline and the operator UI transcript pane.

**Caveat:** exposed reasoning is a model-generated *summary*, not the literal
internal chain of thought; treat it as self-reported, like `trace_summary`.

**Note on current runs:** with `gpt-4.1` (scenarios 2–4) there is no reasoning
channel at all — `model_turn.content` was `None` on all but end-of-run
summary turns. See each run's README under `runs/`.
