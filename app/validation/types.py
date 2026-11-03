"""统一校验结果类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def issue(kind: str, message: str, *, severity: str = "error",
          line: int | None = None, **extra: Any) -> dict:
    out = {"kind": kind, "message": message, "severity": severity}
    if line is not None:
        out["line"] = line
    out.update(extra)
    return out


@dataclass
class ValidationResult:
    object_type: str
    object_key: str = ""
    object_id: int | None = None
    stage: str = ""
    ok: bool = True
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    engine: str = ""
    checked_at: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.now().isoformat(timespec="seconds")

    def error(self, kind: str, message: str, **kw) -> None:
        self.errors.append(issue(kind, message, severity="error", **kw))
        self.ok = False

    def warn(self, kind: str, message: str, **kw) -> None:
        self.warnings.append(issue(kind, message, severity="warning", **kw))

    def as_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "object_key": self.object_key,
            "stage": self.stage,
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "engine": self.engine,
            "checked_at": self.checked_at,
            "metadata": self.metadata,
        }
