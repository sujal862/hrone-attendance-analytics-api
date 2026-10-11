import os
from concurrent.futures import ThreadPoolExecutor

os.environ["MONGO_DB"] = "attendance_test_db"

from fastapi.testclient import TestClient
from app.main import app, db


def employee(code="EMP9001", shift_start="09:30", shift_end="18:30"):
    return {"emp_code": code, "name": "Test User", "email": f"{code.lower()}@example.com",
            "department": "QA", "shift_start": shift_start, "shift_end": shift_end,
            "joined_on": "2026-07-01"}


def test_rules_crud_audit_and_analytics():
    db.employees.drop(); db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/employees", json=employee()).status_code == 201
        # 09:40:01 IST truncates to 09:40:01 and is late by ten whole minutes.
        pin = 1783397401000
        r = c.post("/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": pin})
        assert r.status_code == 201 and r.json()["late_minutes"] == 10
        assert c.post("/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": pin}).status_code == 409
        out = pin + 3 * 3_600_000
        r = c.post("/attendance/punch-out", json={"emp_code": "EMP9001", "punched_at": out})
        assert r.status_code == 200 and r.json()["half_day"] is True
        fixed = pin - 601_000
        r = c.patch("/attendance/EMP9001/2026-07-07", json={"punch_in": fixed, "reason": "clock correction", "regularized_by": "hr"})
        assert r.status_code == 200 and len(r.json()["history"]) == 1
        assert c.get("/analytics/employees/EMP9001/monthly?month=2026-07").status_code == 200
        assert len(c.get("/analytics/departments/QA/trend?from=2026-07-01&to=2026-07-07").json()["items"]) == 7


def test_concurrent_punch_in_has_one_winner():
    db.employees.drop(); db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.post("/employees", json=employee("EMP9002")).status_code == 201
        payload = {"emp_code": "EMP9002", "punched_at": 1783398600000}
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(lambda _: c.post("/attendance/punch-in", json=payload).status_code, range(6)))
        assert statuses.count(201) == 1
        assert statuses.count(409) == 5


def test_validation():
    db.employees.drop(); db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.post("/employees", json=employee("bad")).status_code == 422
        assert c.get("/attendance?page=0").status_code == 422
        assert c.get("/attendance?date_from=2026-07-02&date_to=2026-07-01").status_code == 422
        assert c.post("/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": 123}).status_code == 422
