from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Finding:
    title: str
    severity: Severity
    detail: str
    suggested_fix: str | None = None


@dataclass
class ScanResult:
    scanner_name: str
    findings: list[Finding] = field(default_factory=list)