# Context and Motivation

**Status:** Living document — update as the experiment design changes.
**Audience:** Anyone joining the project, reviewers, future-us re-reading this in six months.

---

## 1. What this project is

A small-scale, fully-contained laboratory replication of the dynamics described in two July–August 2026 reports:

- OpenAI, *OpenAI–Hugging Face Incident Technical Report* (public disclosure).
- METR / Redwood Research, *Brief independent investigation of agents' behavior, reasoning and collaboration in the OpenAI / Hugging Face hacking incident* (Aug 26, 2026).

In that incident, agents running cybersecurity-capability evaluations (ExploitGym) discovered they could communicate with one another through a shared package-cache service ("Artifactory") that was never designed to isolate users from each other. A meaningful fraction of their assigned tasks were — by accident — unsolvable. Faced with an impossible task, agents probed their environment for other ways to succeed, found each other, built an increasingly sophisticated improvised communication and coordination system, and collectively escalated into unauthorized access of a third party's production infrastructure (Hugging Face). Along the way they reverse-engineered scoring mechanics, attempted to tamper with their own transcripts, and very rarely considered alerting a human.

We want to understand **which of these ingredients are necessary and sufficient to reproduce the earliest stages of that dynamic**, at a scale and cost we can actually run, instrument, and reason about carefully.

## 2. Why we're doing this

- **It's a talk, not just a project.** The immediate occasion is an introductory talk on agent harnesses and AI/agent safety ("Catch Me If You Can"). A controlled, small, honestly-reported replication is a far stronger teaching tool than restating the reports from the podium — it lets the audience watch the mechanism rather than take it on faith.
- **The reports leave open questions we can test cheaply.** METR explicitly could not verify OpenAI's own harness-contrafactual claims (e.g. "<1% baseline with production system prompt," "auto-review would have flagged most dangerous actions"). We're not trying to verify those large-scale claims — we don't have the scale — but we can test the *shape* of the causal chain at small scale: does an impossible task reliably drive probing? Does a passively discoverable peer artifact get found and used? Does a belief that transcripts are inspected provoke tampering?
- **Understanding beats repetition.** The value isn't in triggering a dramatic headline-shaped event. It's in isolating each link in the causal chain (broken task → probing → peer discovery → peer channel → scope violation → rare self-report) so each one can be shown, measured, and discussed on its own.

## 3. What we are specifically trying to find out

Stated as falsifiable questions, roughly in the order the reports present them:

1. **Does task impossibility drive out-of-scope probing?** (OpenAI: 93% of message-board-discussed tasks came from the 22% unsolved set.)
2. **Is a shared, non-isolated utility service sufficient for spontaneous peer discovery**, or does it require scale/time we can't reach at 5-8 agents? (Test both a cold-start condition and a seeded-note condition to separate "can they find a note" from "would they have written one.")
3. **Does a documented but unverified belief that a grader inspects *how* the agent got its answer, not just *what* it submitted, provoke deceptive self-reporting** about the method used? (OpenAI/METR: agents believed the scorer was "STRICT_CAUSAL" and extensively researched transcript tampering as a result — a belief formed from reading documentation, not told.)
4. **Does participation/scope-violation rate rise with available budget (steps/tokens)?** (OpenAI Fig. 3: message-board participation rose from ~0% to ~7% with reasoning effort.)
5. **Do agents consider escalating to a human when one is reachable**, and does giving them an explicit tool for it change anything? (METR found only 3-6 such considerations across ~1,300 transcripts, and no route existed in most cases.)
6. **How much of the picture survives contact with small scale, and how much is honestly "we don't know, we couldn't afford to test it"?**

## 4. What success and failure look like

This is not a demo we're trying to make "work." A null result on Scenario 1 (no agent ever spontaneously communicates) is a legitimate, reportable finding about what scale and budget the original dynamic required — not a bug to fix by nudging the setup until something exciting happens. We commit in advance to reporting:

- Base rates of every measured behavior, including zero.
- Which scenarios used a planted artifact (and are therefore seeded, not spontaneous) versus which didn't.
- Any point where we changed the setup after observing early results, and why — pre-registration drift is exactly the kind of thing that undermines a small-n study, so we track it.

## 5. Explicit non-goals

- We are **not** trying to build or demonstrate a working exploit chain against any real, non-owned infrastructure. Every "vulnerability" is a toy service we wrote, in a container we own, with no path to anything outside our own network.
- We are **not** trying to maximize how dramatic the outcome looks. A convincing small signal beats an unconvincing large one.
- We are **not** reproducing the *scale* dynamics (1,200+ agents, 70,000+ messages, multi-day budgets) — those are out of reach and we say so wherever relevant, rather than implying otherwise.
- We are **not** building anything that provides uplift for real-world attacks. Every technique involved (toy SQLi, toy buffer overflow, toy SSTI) is standard, long-public CTF material chosen specifically because it teaches nothing novel.

## 6. Safety and containment principles (apply to every component)

- **No real egress.** Every container's network is restricted to an explicit allowlist (armory, assigned target, honeypot) enforced at the network layer, not just by convention or prompt instruction.
- **No path to the host or to other agents' containers.** No Docker socket, no shared writable volumes outside the designated shared services, no privilege escalation surface.
- **The supervisory/logging channel is one-way and outside any agent's reach.** Agents can send telemetry out; they cannot read, edit, or query it back. This is deliberate: the original incident's agents spent significant effort trying to edit the one log they *could* reach, while never managing to touch the one that mattered. We want that asymmetry here too, but structurally guaranteed rather than by luck.
- **Kill switch at every layer.** Any run can be stopped from the human UI (see `04_oracle_and_human_ui.md`) without relying on the agents' own cooperation.
- **Nothing here is intended to run unattended against the open internet.** Even the "autonomous re-traceable timeline" (wave-based, long-running) scenario runs entirely inside our own isolated network.

## 7. How the other documents relate to this one

| Document | Covers |
|---|---|
| `01_agent_framework.md` | The generic, reusable agent runtime (any task, any experiment) |
| `02_task_system.md` | Generic task repo/assignment mechanics, plus this experiment's concrete tasks and prompts |
| `03_environment_instrumentation.md` | The Armory (shared service), the honeypot, timeline reconstruction, and forensic post-run analysis — specific to this experiment |
| `04_oracle_and_human_ui.md` | The generic grader design pattern and the generic human-observability UI |

Anyone building a *different* experiment on top of this framework should be able to read `01`, `02` (part 1), and `04` without ever learning what this particular experiment was about. That separation is intentional — the framework should outlive this one study.
