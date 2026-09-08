from sysdoc.core.models import Finding, Severity
from sysdoc.core.orchestrator import Orchestrator
from sysdoc.scanners.base import Scanner


class DummyScanner(Scanner):
    name = "dummy"

    def run(self) -> list[Finding]:
        return [Finding(title="Test finding", severity=Severity.OK, detail="Wiring works correctly")]


def test_orchestrator():
    orchestrator = Orchestrator([DummyScanner()])
    results = orchestrator.run_all()
    assert len(results) == 1
    assert results[0].scanner_name == "dummy"
    assert results[0].findings[0].title == "Test finding"
    print("Smoke test passed:", results)
