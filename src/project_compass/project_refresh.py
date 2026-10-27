from datetime import datetime, timezone

from .candidate_extraction import current_file_snapshots
from .project_scanner import scan_project


class ProjectRescan:
    """Explicit model-free scan; caller serializes canonical workspace changes."""
    def __init__(self, workspace, events):
        self.workspace = workspace
        self.events = events

    def refresh(self):
        before = {event.metadata.get('source_ref'): event.id for event in current_file_snapshots(self.events)}
        previous_ids = {event.id for event in self.events}
        scanned = scan_project(self.workspace.project_path)
        self.workspace.save(scanned, {'source': 'complete_rescan'}, complete_scan=True)
        self.events[:] = self.workspace.load()
        after = {event.metadata.get('source_ref'): event.id for event in current_file_snapshots(self.events)}
        return {'enabled': True, 'last_checked_at': datetime.now(timezone.utc).isoformat(),
                'updated': previous_ids != {event.id for event in self.events},
                'file_count': len(after), 'changed_files': sum(before[p] != after[p] for p in before.keys() & after.keys()),
                'new_files': len(after.keys() - before.keys()), 'unavailable_files': len(before.keys() - after.keys()),
                'scope': 'configured document paths and Git; no model call; unavailability is not proof of deletion'}
