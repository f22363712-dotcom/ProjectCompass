"""Persist exact direction proposals before acknowledging separate rule progress."""
from .directions import scan_explicit_directions
from .extraction_progress import ExtractionProgress


def record_direction_candidates(events,workspace=None,*,session_members=None):
    progress=ExtractionProgress(workspace.state_path.parent/'direction-rules',
        namespace='literal-direction-v1:scan-v1') if workspace is not None else None
    proposals,updates,status=scan_explicit_directions(events,session_members=session_members,
        cursors=progress.load() if progress else {})
    if proposals:
        if workspace is not None:
            workspace.save(proposals)
            events[:]=workspace.load()
        else:events.extend(proposals)
    # If this write fails, persisted proposals survive and the next pass dedups
    # them before retrying. A failed proposal save never reaches this point.
    if progress:progress.acknowledge(updates)
    return {'direction_scan':status,'new_direction_proposals':len(proposals),'updated':bool(proposals)}
