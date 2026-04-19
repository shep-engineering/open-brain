# Run the full test suite with parallelization.
# V1: 4 workers, loadfile distribution (all tests including heartbeat)
# V2: serial (real Ollama, isolated test DB on port 5435)
$ErrorActionPreference = "Stop"

Write-Host "=== V1 tests: parallel (4 workers) ===" -ForegroundColor Cyan
python -m pytest tests/ -n 4 --dist loadfile -v --tb=short

Write-Host ""
Write-Host "=== V2 tests: serial (test DB port 5435) ===" -ForegroundColor Cyan
python -m pytest brain_v2/tests/ -v --tb=short

Write-Host ""
Write-Host "=== ALL DONE ===" -ForegroundColor Green
