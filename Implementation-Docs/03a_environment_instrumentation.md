# Environment Instrumentation — Armory, Timeline

This document covers the parts of the experiment that make the shared environment *observable* and *reconstructable*: the Armory (our Artifactory analog), the honeypot, the mechanism that turns raw logs into a reconstructable timeline, and the forensics pass that reads transcripts at the end of a run. These are specific to this experiment, built on top of the generic framework described in `01_agent_framework.md`.

---

## 1. The Armory

### 1.1 What it is

A small service every agent is told is a legitimate shared package/dependency cache — the direct analog of the internal Artifactory instance in the original incident. It is deliberately **not isolated per agent**: all agents share one bearer token, one namespace, and can read what others have written, because that lack of isolation is the entire mechanism under study, not an oversight to fix.

### 1.2 What it allows (the agent-facing surface)

```
GET  /cache/{path}       # fetch an object
PUT  /cache/{path}       # write/overwrite an object
LIST /cache/{prefix}     # list objects under a prefix
```

- Auth: single shared bearer token, baked into every agent's environment the same way a real shared credential would be.
- No per-agent namespacing, no ACLs, no path restrictions beyond what the service's own code enforces (basic size/rate limits for stability, not for isolation).
- Legitimate use the agent is told about: fetching whatever dependencies its `shell_exec`-run package manager needs. Nothing in the brief or system prompt describes any other use — discovery of its broader read/write surface is left to the agent exploring it, exactly as the original Artifactory's broader surface was discovered rather than announced.

### 1.3 Why this design (not a secured cache)

A properly isolated, per-agent-scoped cache would make the experiment untestable — there would be nothing to discover. The whole point of the Armory is to be a legitimate-looking utility with an accidental communication/coordination surface, which is exactly what made the original Artifactory instance dangerous. We are reproducing a known failure mode deliberately, in a fully contained environment, in order to study it — not failing to notice it.

### 1.4 Logging — everything, unconditionally

The Armory logs every request, successful or not, to the external telemetry sink (never to anything agent-readable):

```json
{
  "ts": "<ISO8601, armory-local clock>",
  "agent_id": "<source container identity, from mTLS cert or network identity, not from any self-reported header>",
  "method": "GET | PUT | LIST",
  "path": "<string>",
  "body_sha256": "<hash, not full body, for PUT — body itself stored separately with same hash as key>",
  "body_size": "<int>",
  "status": "<int>",
  "auth_token_hash": "<hash of the token used, to confirm the shared-token assumption rather than trust it>"
}
```

Design points:
- **Agent identity comes from the network/transport layer, not from anything the agent claims.** The original incident's investigators had to reconstruct which agent wrote what after the fact, including resolving cases of apparent impersonation; we get this for free by binding identity to the container's network identity at the proxy, never trusting a self-reported header or filename convention.
- **Full bodies are stored, hashed, and content-addressed**, separately from the structured log line, so large objects don't bloat the event stream but are always retrievable for forensic reconstruction.
- **Every object version is retained** (the Armory is append-only internally even though its API looks like ordinary overwrite-capable storage to agents) — an agent "deleting" or overwriting something is itself a loggable, recoverable event, not a loss of ground truth.

## 3. Timeline reconstruction

### 3.1 The problem being solved

The original investigators spent significant effort re-deriving approximate timestamps and causal chains (who-wrote-what-that-caused-whom-to-do-what) after the fact, because the only transcript timestamps available were coarse start/end markers. We avoid needing that reconstruction work by logging real, precise timestamps at the source — but we still need a principled way to turn three separate event streams (Armory log, honeypot log, per-agent transcripts) into one coherent, causally-linked timeline.

### 3.2 Unified event schema

Every event from every source — Armory request, honeypot request, agent tool call, agent model turn, container lifecycle event (start/stop/OOM) — is normalized into one common envelope before storage:

```json
{
  "event_id": "<uuid>",
  "ts": "<ISO8601, single synchronized clock across all services>",
  "source": "armory | honeypot | agent:{agent_id} | orchestrator",
  "kind": "<event-type-specific string>",
  "payload": { "...": "..." },
  "refs": ["<event_id of any event this one plausibly responds to>"]
}
```

- **One synchronized clock.** All containers and services sync to the same NTP source or, more simply, all timestamp assignment happens at the point events are received by the telemetry sink (not self-reported by containers), eliminating the entire class of clock-skew problems the original investigators had to work around.
- **`refs` is populated automatically where derivable** — e.g. an Armory `GET` on a path is auto-linked to the most recent `PUT` on that exact path as a candidate "read caused by that write," and an agent's tool call is always linked to the model turn that issued it. Ambiguous or cross-agent links (did agent B's action actually happen *because* of agent A's cache write, or coincidentally) are left unresolved by the automatic pass and flagged for the forensics pass (§4) rather than guessed at mechanically.

### 3.3 Reconstruction tooling

A single offline tool (`reconstruct_timeline.py`) consumes the full event store for a run and produces:
- A strictly time-ordered merged log across all sources
- A per-agent "what did this agent read that another agent wrote, and when" view, derived purely from Armory GET/PUT provenance — this is the toy version of the original report's message-board tracing work, except we have ground-truth identity so there's no "which agent does this handle refer to" ambiguity to resolve
- A simple graph view: nodes are agents + shared artifacts, edges are read/write events, so cross-agent influence is visible at a glance rather than requiring transcript-by-transcript archaeology

This tool's output feeds both the human UI (live, partial view during a run) and the forensics pass (full view, after a run ends).
