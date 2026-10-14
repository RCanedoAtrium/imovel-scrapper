from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

EvidenceLevel = Literal["confirmed", "likely", "estimated", "no_match"]


@dataclass
class SourceArtifact:
    source: str
    path: str
    resource_id: str = ""
    url: str = ""
    competence: str = ""
    published_at: str = ""
    size: int = 0
    sha256: str = ""
    status: str = "downloaded"


@dataclass
class StageResult:
    stage: str
    status: Literal["success", "warning", "failed"] = "success"
    artifacts: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunManifest:
    run_id: str
    command: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    status: str = "running"
    stages: list[dict[str, Any]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = datetime.now(timezone.utc).isoformat()
