# Run the full test suite with parallelization where safe.
# V1 parallel: 4 workers, loadfile distribution (excludes serial-marked tests)
# V1 serial: heartbeat agent + any other serial-marked tests
# V2: serial (real Ollama, isolated test DB on port 5435)
$ErrorActionPreference = "Stop"

Write-Host "=== V1 tests: parallel (4 workers, excludes @serial) ===" -ForegroundColor Cyan
python -m pytest tests/ -n 4 --dist loadfile -m "not serial" -v --tb=short

Write-Host ""
Write-Host "=== V1 tests: serial (@serial marked) ===" -ForegroundColor Cyan
python -m pytest tests/ -m "serial" -v --tb=short

Write-Host ""
Write-Host "=== V2 tests: serial (test DB port 5435) ===" -ForegroundColor Cyan
python -m pytest brain_v2/tests/ -v --tb=short

Write-Host ""
Write-Host "=== ALL DONE ===" -ForegroundColor Green
