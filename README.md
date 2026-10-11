# Employee Attendance & Analytics API

FastAPI/MongoDB implementation of the HROne assignment: employee management, race-safe punches, audited corrections, paginated attendance, aggregation analytics, and query-plan explanations.

## Setup and run

Prerequisites: Python 3.11+ and MongoDB 6.0+ (MongoDB 7 recommended).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
python sample_seed.py
uvicorn app.main:app --port 8000
```

On macOS/Linux activate with `source .venv/bin/activate` and use `export`. Environment variables take precedence over an optional local `.env`; never commit that file. A local MongoDB can be started with `docker run --name hrone-mongo -p 27017:27017 -d mongo:7`. No Dockerfile belongs in this repository.

The seed command replaces both collections with nine illustrative records. Indexes are created idempotently at startup. Readiness is `GET /health`; interactive docs are at `/docs`.

```powershell
curl http://localhost:8000/employees?page=1&page_size=20
curl -X POST http://localhost:8000/attendance/punch-in -H "Content-Type: application/json" -d '{"emp_code":"EMP0001","punched_at":1783398300000}'
curl "http://localhost:8000/analytics/departments/summary?month=2026-07"
```

## Verify

```powershell
python -m py_compile app/main.py
python -m pip install pytest httpx
python -m pytest -q
```

Tests require running MongoDB and use `attendance_test_db`. The synchronous PyMongo endpoints run in FastAPI's worker pool. Analytics use MongoDB pipelines and stored derived values. Authentication is intentionally absent because the contract defines `regularized_by` as free text.
