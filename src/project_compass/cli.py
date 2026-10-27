from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .evidence_store import EvidenceStore
from .env_loader import load_env_file
from .dashboard import build_dashboard_state
from .codex_extractor import CodexExtractor
from .evidence_linking import build_evidence_index, confirm_relation
from .fact_engine import derive_facts
from .investigator import investigate
from .jev_provider import JevDecisionProvider
from .sample_data import sample_events
from .project_scanner import scan_project
from .project_workspace import ProjectWorkspace
from .projections import build_views
from .web_server import run_dashboard
from .native_codex import NativeCodexIntake
from .meterless_memory import MeterlessMemory
from .zetaris_provider import ZetarisEvidenceProvider
from .project_refresh import ProjectRescan
from .source_currentness import source_states, needs_source_review
from .extraction_jobs import ExtractionJobs
from .agent_inbox import AgentInbox


QUESTION = "Why is the project in its current state, and what should happen next?"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _log_model_usage(entry: dict) -> None:
    usage_path = PROJECT_ROOT / "logs/model-usage.jsonl"
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    with usage_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _review_callback(events, project_workspace: ProjectWorkspace, project_path: str, confirmed_relations, review_history, fact_reviews):
    from .directions import DIRECTION_ACTIONS, direction_review_fields
    confirmed = list(confirmed_relations)
    history = list(review_history)
    accepted = list(fact_reviews)

    def review(action: str, target_id: str, reviewer: str) -> dict:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        next_confirmed = list(confirmed)
        next_history = list(history)
        next_accepted = list(accepted)
        if action == "confirm_relation":
            index = build_evidence_index(events)
            candidate = next((item for item in index.candidate_relations if item.id == target_id), None)
            if candidate is None:
                raise ValueError("candidate relation not found")
            relation = confirm_relation(candidate, reviewer)
            if any(existing.id == relation.id for existing in confirmed):
                raise ValueError("relation already confirmed")
            next_confirmed.append(relation)
            next_history.append({
                "action": action,
                "relation_id": relation.id,
                "fact_id": relation.fact_id,
                "evidence_id": relation.evidence_id,
                "reviewer": reviewer,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            })
        elif action == "accept_fact" or action in DIRECTION_ACTIONS:
            fact = next((item for item in derive_facts(events) if item.id == target_id), None)
            if fact is None:
                raise ValueError("fact not found")
            if needs_source_review(source_states(events, [fact])[fact.id]):
                raise ValueError('source is superseded or unavailable; review the current source before promoting a fact')
            direction_fields=direction_review_fields(action,fact,accepted) if action in DIRECTION_ACTIONS else {}
            if action=='accept_fact' and any(item.get("fact_id") == fact.id for item in accepted):
                raise ValueError("fact already accepted")
            next_accepted.append({
                "action": "accept_fact",
                "fact_id": fact.id,
                "subject_key": fact.subject_key,
                "reviewer": reviewer,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                **direction_fields,
            })
            next_history.append(dict(next_accepted[-1]))
            next_history[-1]['action']=action
        else:
            raise ValueError("unsupported review action")
        project_workspace.save_review_state(next_confirmed, next_history, next_accepted)
        confirmed[:] = next_confirmed
        history[:] = next_history
        accepted[:] = next_accepted
        return build_dashboard_state(events, project_path, confirmed, history, accepted)

    return review, confirmed, history, accepted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Project Compass golden path")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--sample", action="store_true")
    source.add_argument("--input", type=Path, help="JSON evidence array")
    source.add_argument("--codex-jsonl", type=Path, help="Codex public session events in JSONL")
    source.add_argument("--scan", type=Path, help="Scan an existing project directory")
    source.add_argument("--open", type=Path, help="Open a previously persisted project state")
    parser.add_argument("--scan-codex-jsonl", type=Path, action="append", default=[], help="JSONL session to include with --scan")
    parser.add_argument("--state", type=Path, help="Optional persisted evidence state output")
    parser.add_argument("--evidence-index", action="store_true", help="Print evidence catalog and relation suggestions")
    parser.add_argument("--jev", action="store_true", help="Use Jev when TYPESAFE_API_KEY is available")
    parser.add_argument("--meterless", action="store_true", help="Use the pinned local Meterless World Model for web investigation")
    parser.add_argument('--zetaris-bundle', type=Path, help='Use a configured official local Zetaris bundle for web source retrieval')
    parser.add_argument("--extract-codex", action="store_true", help="Explicitly extract a bounded candidate batch using saved Codex CLI auth")
    parser.add_argument('--enable-extraction', action='store_true', help='Enable bounded manual candidate jobs in the real web board')
    parser.add_argument('--auto-extract', action='store_true', help='Opt into automatic bounded candidate jobs; implies --enable-extraction')
    parser.add_argument('--extraction-max-batches', type=int, default=2, help='Maximum admitted live batches for this server start')
    parser.add_argument('--extraction-max-tokens', type=int, default=80000, help='Stop live admissions after this actual known-token threshold; a request may cross it')
    parser.add_argument("--member-id", help="Developer identity for public user/assistant collaboration")
    parser.add_argument("--codex-thread", help="Explicit native Codex thread UUID to capture")
    parser.add_argument('--agent-access-file', type=Path, help='Private project-bound Agent HTTP client config; requires a real web workspace')
    parser.add_argument('--codex-tools', action='store_true', help='Opt into bounded public exec text call/result pairs from the selected thread')
    parser.add_argument("--codex-sessions-root", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions", help="Native rollout root for the selected thread")
    parser.add_argument("--web", action="store_true", help="Start the local personal/team dashboard")
    parser.add_argument('--reason-preview',type=Path,help='Read a project-bound private reason experiment as a model-free, read-only preview')
    parser.add_argument("--port", type=int, default=8787, help="Dashboard port for --web")
    parser.add_argument("--workspace-root", type=Path, default=Path(".project-memory"), help="Local root for persisted project states")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
        help="Local credentials file; process environment variables take precedence",
    )
    args = parser.parse_args(argv)
    try:
        load_env_file(args.env_file)
        args.enable_extraction = args.enable_extraction or args.auto_extract
        if not any((args.sample, args.input, args.codex_jsonl, args.scan, args.open)):
            if os.environ.get('SAMPLE_MODE', '').strip().casefold() == 'true':
                args.sample = True
            else:
                parser.error('select a source (--sample, --input, --codex-jsonl, --scan, --open), or set SAMPLE_MODE=true')
        if args.reason_preview and not (args.web and (args.scan or args.open)):
            raise ValueError('--reason-preview requires a real web workspace; sample mode cannot read private reason previews')
        if args.sample and (args.jev or args.meterless or args.zetaris_bundle or args.extract_codex or args.enable_extraction or args.codex_thread or args.codex_tools or args.scan_codex_jsonl or args.agent_access_file or args.reason_preview):
            raise ValueError('sample mode cannot enable model, sponsor or native project inputs')
        if args.agent_access_file and not (args.web and (args.scan or args.open)):
            raise ValueError('--agent-access-file requires --web and a real --scan or --open workspace')
        if args.meterless and not (args.web and (args.scan or args.open)):
            raise ValueError("--meterless requires --web with --scan or --open")
        if args.zetaris_bundle and not (args.web and (args.scan or args.open)):
            raise ValueError('--zetaris-bundle requires --web with --scan or --open')
        if args.enable_extraction:
            if not (args.web and (args.scan or args.open) and args.member_id and args.member_id.strip()):
                raise ValueError('--enable-extraction requires a real web project and explicit --member-id')
            if args.extract_codex or args.extraction_max_batches < 1 or args.extraction_max_tokens < 1:
                raise ValueError('choose one-shot or live extraction, with positive live budgets')
        if args.extract_codex and (not (args.scan or args.open) or not args.member_id or not args.member_id.strip()):
            raise ValueError("--extract-codex requires --scan or --open and an explicit --member-id")
        if args.codex_thread and (not (args.scan or args.open) or not args.member_id or not args.member_id.strip()):
            raise ValueError("--codex-thread requires --scan or --open and an explicit --member-id")
        if args.codex_tools and not args.codex_thread:
            raise ValueError('--codex-tools requires an explicitly selected --codex-thread')
        project_workspace = None
        if args.sample:
            events = sample_events()
            if args.web:
                project_workspace = ProjectWorkspace(args.workspace_root / 'sample', PROJECT_ROOT / 'examples')
                project_path = str(project_workspace.project_path)
                if not project_workspace.exists():
                    project_workspace.save(events, {'source': 'sample', 'synthetic': True})
                events = project_workspace.load()
        elif args.codex_jsonl:
            events = EvidenceStore.load_jsonl(args.codex_jsonl).list()
        elif args.scan:
            events = scan_project(args.scan, codex_paths=args.scan_codex_jsonl)
            project_workspace = ProjectWorkspace(args.workspace_root, args.scan)
            project_path = str(project_workspace.project_path)
            project_workspace.save(events, {"source": "scan"}, complete_scan=True)
            events = project_workspace.load()
        elif args.open:
            project_workspace = ProjectWorkspace(args.workspace_root, args.open)
            project_path = str(project_workspace.project_path)
            events = project_workspace.load()
        else:
            events = EvidenceStore.load(args.input).list()
        intake_status = {"enabled": False}
        session_members = {}
        def record_direction_candidates():
            from .direction_intake import record_direction_candidates as record
            from .user_task_intake import record_user_task_candidates
            direction=record(events,project_workspace,session_members=session_members)
            tasks=record_user_task_candidates(events,project_workspace,session_members=session_members)
            return {**direction,**tasks,'updated':direction['updated'] or tasks['updated']}
        refresh_callback = None
        if args.codex_thread:
            native_intake = NativeCodexIntake(args.codex_sessions_root, project_path, args.codex_thread, args.member_id, include_tools=args.codex_tools)
            session_members[args.codex_thread] = args.member_id.strip()
            def refresh_callback():
                status=native_intake.sync(project_workspace, events)
                direction=record_direction_candidates()
                return {**status,**direction,'updated':status.get('updated',False) or direction['updated']}
            intake_status = refresh_callback()
            session_members[args.codex_thread] = args.member_id.strip()
            print(json.dumps({"native_intake": intake_status}, ensure_ascii=False), file=sys.stderr)
        else:
            def refresh_callback():
                return {'enabled':False,**record_direction_candidates()}
            intake_status=refresh_callback()
        if args.extract_codex:
            extractor = CodexExtractor(usage_logger=_log_model_usage, member_id=args.member_id.strip(),
                                       legacy_cache_dir=args.workspace_root / 'extraction-cache', session_members=session_members)
            proposals = extractor.extract(events, project_workspace.state_path.parent / 'extraction-cache')
            if proposals:
                project_workspace.save(proposals)
            events = project_workspace.load()
            extractor.acknowledge(events)
            print(json.dumps({"extraction": extractor.last_result, "proposals": len(proposals)}, ensure_ascii=False), file=sys.stderr)
        if args.web:
            if not (args.scan or args.open or args.sample):
                raise ValueError("--web requires --scan, --open or --sample")
            if project_workspace is None:
                raise ValueError("web mode requires a persisted project workspace")
            confirmed_relations, review_history, fact_reviews = project_workspace.load_review_state()
            review_callback, confirmed_relations, review_history, fact_reviews = _review_callback(
                events, project_workspace, str(project_path), confirmed_relations, review_history, fact_reviews
            )
            reason_preview=None
            if args.reason_preview:
                from .reason_preview import ReasonPreview
                reason_preview=ReasonPreview.load(args.reason_preview,project_path,events)
            run_dashboard(
                events,
                project_path=str(project_path),
                port=args.port,
                confirmed_relations=confirmed_relations,
                review_history=review_history,
                fact_reviews=fact_reviews,
                review_callback=review_callback,
                refresh_callback=refresh_callback,
                intake_status=intake_status,
                memory=MeterlessMemory(args.workspace_root / 'meterless', project_path) if args.meterless else None,
                sample_mode=args.sample,
                zetaris=ZetarisEvidenceProvider(args.zetaris_bundle, project_path) if args.zetaris_bundle else None,
                rescan_callback=ProjectRescan(project_workspace, events).refresh if not args.sample else None,
                extraction_jobs=ExtractionJobs(project_workspace, events, args.member_id,
                    usage_logger=_log_model_usage, max_batches=args.extraction_max_batches,
                    max_tokens=args.extraction_max_tokens, automatic=args.auto_extract, session_members=session_members) if args.enable_extraction else None,
                agent_inbox=AgentInbox.from_file(args.agent_access_file, project_workspace, events) if args.agent_access_file else None,
                reason_preview=reason_preview,
            )
            return 0
        if args.evidence_index:
            usage_path = Path("logs/model-usage.jsonl")

            def log_usage(entry: dict) -> None:
                usage_path.parent.mkdir(parents=True, exist_ok=True)
                with usage_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

            provider = JevDecisionProvider(os.environ.get("TYPESAFE_API_KEY"), usage_logger=log_usage) if args.jev else None
            facts = derive_facts(events)
            index = build_evidence_index(events, facts, provider=provider)
            if project_workspace is not None:
                persisted_relations, _, _ = project_workspace.load_review_state()
                index = index.with_confirmed_relations(persisted_relations)
            print(json.dumps(index.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.scan:
            fact_reviews = project_workspace.load_review_state()[2] if project_workspace is not None else []
            accepted_fact_ids = [item["fact_id"] for item in fact_reviews if item.get("action") == "accept_fact"]
            print(json.dumps(build_views(events, accepted_fact_ids=accepted_fact_ids, session_members=session_members), ensure_ascii=False, indent=2))
            return 0
        if args.state:
            store = EvidenceStore(events)
            store.save(args.state)
        print(json.dumps(investigate(events, QUESTION).to_dict(), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid evidence JSON or state: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
