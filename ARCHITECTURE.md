# Architecture and trust boundaries

The local Python CLI selects a project and loads its append-only workspace. The web server projects the same events into member-owned candidate views and an owner-reviewed team view. The timeline orders observations; an ordered edge is not proof of causality.

| Stage | Source code | Responsibility |
| --- | --- | --- |
| Intake | src/project_compass/project_scanner.py, native_codex.py, agent_http.py | Bounded project files, public Agent messages and normalized authenticated reports |
| Persistence | project_workspace.py, project_refresh.py | Project isolation, append-only events and rescan observations |
| Candidates | candidate_extraction.py, direction_intake.py, user_task_intake.py | Exact-source proposals; bounded literal rules do not establish semantic completeness |
| Projection | projections.py, dashboard.py, source_currentness.py | Personal/team scope, changed-source warnings, retained conflict and review history |
| Review | web_server.py, cli.py | Owner actions stage a durable save before exposing the new in-memory state |
| Investigation | investigator.py, raw_source_investigation.py, reason_preview.py | Question-scoped findings and provenance, explicit gaps, optional cached proposed interpretations |
| Sponsor reads | meterless_memory.py, zetaris_provider.py, zetaris_worker.py | World Model fact/support reads and verified raw-source retrieval |
| UI | src/project_compass/web/ | Personal/team views, horizontal trace and source drawers |

Evidence, a claim about it, and an accepted state are separate records. Agent credentials may append evidence or proposals; they cannot silently accept owner facts. A public assistant report stays a report until reviewed. A successful historical test does not establish acceptance of a later build. Changed or unavailable documents require renewed review, while prior records remain visible.

Enabled sponsor failures stop investigation rather than returning an unverified fallback. Revision changes during a query invalidate the old answer. Current source checks verify exact records, not the truth of causal interpretation or historical currentness across every version.

## Open product verification

General automatic reason generation and owner review of reason relations are unfinished. The optional private cache preview only illustrates proposed explanations for already-bound targets. Complete long-history semantic recall and cross-source causal reconciliation remain unproven. Native tool capture is bounded and is not a claim to capture every command in an Agent ecosystem. A genuine multi-human collaboration trial and final learner feedback are still outstanding.

The source-only offline path is separate from an independent full sponsor backend installation. Real integration was exercised locally against an existing authorized backend; this does not establish fresh backend provisioning or a latency guarantee. No real project data is included in this candidate.
