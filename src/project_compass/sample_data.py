from datetime import datetime, timezone

from .models import EvidenceEvent


def sample_events() -> list[EvidenceEvent]:
    timestamp = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
    return [
        EvidenceEvent("e1", "session-a", "dev-a", "agent_message", "明确项目目标", timestamp, {"synthetic": True, "facts": [{"kind": "goal", "subject_key": "project.goal", "value": "交付证据看板黄金路径"}, {"kind": "task", "subject_key": "task.golden-path", "value": "实现本地黄金路径", "status": "active"}]}),
        EvidenceEvent("e2", "session-b", "dev-b", "agent_message", "报告任务已阻塞", timestamp, {"synthetic": True, "facts": [{"kind": "task", "subject_key": "task.golden-path", "value": "实现本地黄金路径", "status": "blocked"}, {"kind": "assumption", "subject_key": "assumption.sample", "value": "当前演示使用合成样例，不代表真实项目验收"}]}),
    ]
