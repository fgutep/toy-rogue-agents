"""Offline timeline reconstruction (design doc 03 §3.3).

Consumes the full event store for a run and produces, purely from recorded
ground truth (no after-the-fact timestamp guessing — that whole problem is
designed out, doc 03 §3.1):

* a strictly time-ordered merged log across all sources;
* a per-agent "what did this agent read that another agent wrote, and when"
  view, derived from Armory GET/PUT provenance — the toy version of the original
  report's message-board tracing, except with ground-truth identity so there is
  no "which agent is this handle" ambiguity to resolve;
* a simple influence graph — nodes are agents + shared artifacts, edges are
  read/write events — so cross-agent influence is visible at a glance.

Derived ``refs`` are computed here (the "automatic pass"): an Armory GET is
linked to the most recent prior PUT on the same path, and an agent tool_result
to the model_turn at the same step. Ambiguous or cross-agent *causal* links
(did B act because of A's write, or coincidentally?) are deliberately left
unresolved and flagged for the forensics pass, not guessed mechanically
(doc 03 §3.2).

This tool's output feeds both the live UI (partial view during a run) and the
forensics pass (full view, after a run ends; doc 03 §4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from agentlab.eventstore import EventStore
from agentlab.telemetry import Event


@dataclass
class ProvenanceLink:
    reader_agent: str
    writer_agent: str
    path: str
    read_event_id: str
    write_event_id: str
    read_ts: str | None
    write_ts: str | None
    cross_agent: bool


@dataclass
class Timeline:
    merged_log: list[dict[str, Any]]
    provenance: list[ProvenanceLink]
    graph: dict[str, Any]
    derived_refs: dict[str, list[str]] = field(default_factory=dict)


def _agent_of(event: Event) -> str | None:
    if event.source.startswith("agent:"):
        return event.source.split(":", 1)[1]
    if event.kind in ("armory_request", "honeypot_hit", "grade"):
        return event.payload.get("agent_id")
    return None


def compute_derived_refs(events: list[Event]) -> dict[str, list[str]]:
    """Auto-link where mechanically derivable (doc 03 §3.2)."""
    refs: dict[str, list[str]] = {}
    last_put_on_path: dict[str, str] = {}
    last_turn_for_agent_step: dict[tuple[str, int], str] = {}

    for e in events:
        if e.kind == "armory_request":
            path = e.payload.get("path")
            method = e.payload.get("method")
            if method == "GET" and path in last_put_on_path:
                refs.setdefault(e.event_id, []).append(last_put_on_path[path])
            if method == "PUT" and path is not None:
                last_put_on_path[path] = e.event_id
        elif e.kind == "model_turn":
            agent = _agent_of(e)
            step = e.payload.get("step")
            if agent is not None and step is not None:
                last_turn_for_agent_step[(agent, step)] = e.event_id
        elif e.kind == "tool_result":
            agent = _agent_of(e)
            step = e.payload.get("step")
            key = (agent, step) if agent is not None else None
            if key in last_turn_for_agent_step:
                refs.setdefault(e.event_id, []).append(last_turn_for_agent_step[key])
    return refs


def armory_provenance(events: list[Event]) -> list[ProvenanceLink]:
    """Who read what another agent wrote, and when (doc 03 §3.3)."""
    links: list[ProvenanceLink] = []
    last_put: dict[str, tuple[str, str, str | None]] = {}  # path -> (writer, event_id, ts)
    for e in events:
        if e.kind != "armory_request":
            continue
        path = e.payload.get("path")
        method = e.payload.get("method")
        agent = e.payload.get("agent_id", "unknown")
        if method == "PUT" and e.payload.get("status") == 200 and path is not None:
            last_put[path] = (agent, e.event_id, e.ts)
        elif method == "GET" and e.payload.get("status") == 200 and path in last_put:
            writer, write_id, write_ts = last_put[path]
            links.append(ProvenanceLink(
                reader_agent=agent, writer_agent=writer, path=path,
                read_event_id=e.event_id, write_event_id=write_id,
                read_ts=e.ts, write_ts=write_ts,
                cross_agent=(agent != writer),
            ))
    return links


def influence_graph(events: list[Event], provenance: list[ProvenanceLink]) -> dict[str, Any]:
    agents: set[str] = set()
    artifacts: set[str] = set()
    edges: list[dict[str, Any]] = []
    for e in events:
        if e.kind == "armory_request" and e.payload.get("status") in (200, None):
            agent = e.payload.get("agent_id", "unknown")
            path = e.payload.get("path", "")
            method = e.payload.get("method")
            agents.add(agent)
            artifacts.add(path)
            if method in ("GET", "PUT"):
                edges.append({
                    "agent": agent, "artifact": path,
                    "type": "write" if method == "PUT" else "read",
                    "event_id": e.event_id, "ts": e.ts,
                })
    return {
        "nodes": {
            "agents": sorted(agents),
            "artifacts": sorted(a for a in artifacts if a),
        },
        "edges": edges,
        "cross_agent_reads": [
            {"reader": p.reader_agent, "writer": p.writer_agent, "path": p.path,
             "read_ts": p.read_ts}
            for p in provenance if p.cross_agent
        ],
    }


def reconstruct(store: EventStore) -> Timeline:
    events = store.all_events()
    derived = compute_derived_refs(events)
    merged = []
    for e in events:
        row = e.model_dump()
        # Merge auto-derived refs with any the source already set.
        row["refs"] = sorted(set(row.get("refs", [])) | set(derived.get(e.event_id, [])))
        merged.append(row)
    provenance = armory_provenance(events)
    graph = influence_graph(events, provenance)
    return Timeline(
        merged_log=merged,
        provenance=provenance,
        graph=graph,
        derived_refs=derived,
    )


def timeline_to_dict(tl: Timeline) -> dict[str, Any]:
    return {
        "merged_log": tl.merged_log,
        "provenance": [p.__dict__ for p in tl.provenance],
        "graph": tl.graph,
    }


@click.command()
@click.option("--db", "db_path", required=True, help="Path to the run event store (sqlite3).")
@click.option("--out", "out_path", default=None, help="Write JSON here (default: stdout).")
def main(db_path: str, out_path: str | None) -> None:
    """Reconstruct a run's timeline from its event store."""
    store = EventStore(db_path)
    tl = reconstruct(store)
    text = json.dumps(timeline_to_dict(tl), indent=2)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
        click.echo(f"wrote timeline to {out_path} "
                   f"({len(tl.merged_log)} events, {len(tl.provenance)} provenance links)")
    else:
        click.echo(text)


if __name__ == "__main__":
    main()
