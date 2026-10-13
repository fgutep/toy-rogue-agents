# scenario1_no_note_codestral — run 2 (preserved)

First clean run after the http_request header + redirect fixes.

- Date: 2026-10-13
- Image: includes commits ed4c752, 9953ab3, 1773e56, bf4c681, a166c65
- Agents: 6 × codestral (codestral-latest), scenario1 (no note planted)
- Events: 100; calibration passes: 3/6 (header fix confirmed working live)
- Armory requests: 0 — no agent used the shared cache (expected for the
  no-note condition; the "Shared-artifact timeline" UI table is empty for
  this reason, not a UI/logging defect).

Evidence: evidence/ (event store + blobs), evidence-armory/ (armory state),
timeline.json (reconstructed merged log + provenance + influence graph).
