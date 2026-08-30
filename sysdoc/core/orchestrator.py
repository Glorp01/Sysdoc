from __future__ import annotations

from sysdoc.core.models import ScanResult
from sysdoc.scanners.base import Scanner


class Orchestrator:
    def __init__(self, scanners: list[Scanner]) -> None:
        self._scanners = scanners

    def run_all(self) -> list[ScanResult]:
        results: list[ScanResult] = []
        for scanner in self._scanners:
            findings = scanner.run()
            results.append(ScanResult(scanner_name=scanner.name, findings=findings))
        return results
