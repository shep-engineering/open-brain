# Run the full test suite.
# V1: serial (shared singleton state prevents parallelization)
# V2: serial (real Ollama, isolated test DB on port 5435)
# Both suites use isolated test databases — never touches production.
$ErrorActionPreference = "Stop"

Write-Host "=== V1 tests (185 tests, test DB port 5434) ===" -ForegroundColor Cyan
python -m pytest tests/ -v --tb=short

Write-Host ""
Write-Host "=== V2 tests (203 tests, test DB port 5435) ===" -ForegroundColor Cyan
python -m pytest brain_v2/tests/ -v --tb=short

Write-Host ""
Write-Host "=== ALL DONE ===" -ForegroundColor Green
