from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .codex_adapter import CodexAdapter
from .models import EvidenceEvent


def _id(kind: str, value: str) -> str:
    return f"scan-{kind}-{hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()[:12]}"


def scan_project(root: str | Path, codex_paths: list[str | Path] | None = None, max_commits: int = 15) -> list[EvidenceEvent]:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise NotADirectoryError(f"project path is not a directory: {base}")
    events: list[EvidenceEvent] = []
    now = datetime.now(timezone.utc)
    paths = [base / name for name in ("README.md", "CONTEXT.md", "HANDOFF.md", "DECISIONS.md")]
    for relative in ("docs", "docs/reviews", "docs/plans", "docs/adr"):
        directory = base / relative
        if directory.is_dir() and directory.resolve().is_relative_to(base):
            paths.extend(directory.glob("*.md"))
    for path in sorted(set(paths)):
        if not path.is_file() or path.name.startswith(".env") or not path.resolve().is_relative_to(base):
            continue
        raw = path.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        content_hash = hashlib.sha256(raw).hexdigest()
        source_ref = str(path.relative_to(base))
        events.append(EvidenceEvent(
            _id("file", source_ref + ":" + content_hash),
            "project-scan", "scanner", "file_snapshot", content, now,
            {"source_ref": source_ref, "content_hash": content_hash,
             "source_modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()},
        ))
    for path in codex_paths or []:
        events.extend(CodexAdapter.from_jsonl(path))
    try:
        repository = subprocess.run(["git", "-C", str(base), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10, check=False)
        if repository.returncode != 0:
            return events
        # Filter a subproject before the count limit; keep the original root
        # history semantics, including merges and empty release checkpoints.
        scope = [] if Path(repository.stdout.rstrip('\r\n')).resolve() == base else ["--", "."]
        output = subprocess.run(["git", "-C", str(base), "log", f"-{max_commits}", "--format=%H%x09%aI%x09%s", *scope], capture_output=True, text=True, timeout=10, check=False)
        for line in output.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                sha, observed, subject = parts
                events.append(EvidenceEvent(f"git-{sha}", "project-scan", "git", "git_commit", subject, datetime.fromisoformat(observed), {"commit": sha, "source_ref": f"git:{sha}"}))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return events
