import os
from concurrent.futures import ThreadPoolExecutor

os.environ["MONGO_DB"] = "attendance_test_db"

from fastapi.testclient import TestClient
from app.main import app, db
from datetime import datetime, timezone


def employee(code="EMP9001", shift_start="09:30", shift_end="18:30"):
    return {
        "emp_code": code,
        "name": "Test User",
        "email": f"{code.lower()}@example.com",
        "department": "QA",
        "shift_start": shift_start,
        "shift_end": shift_end,
        "joined_on": "2026-07-01",
    }


def test_rules_crud_audit_and_analytics():
    db.employees.drop()
    db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/employees", json=employee()).status_code == 201
        # 09:40:01 IST truncates to 09:40:01 and is late by ten whole minutes.
        pin = 1783397401000
        r = c.post(
            "/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": pin}
        )
        assert r.status_code == 201 and r.json()["late_minutes"] == 10
        assert (
            c.post(
                "/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": pin}
            ).status_code
            == 409
        )
        out = pin + 3 * 3_600_000
        r = c.post(
            "/attendance/punch-out", json={"emp_code": "EMP9001", "punched_at": out}
        )
        assert r.status_code == 200 and r.json()["half_day"] is True
        fixed = pin - 601_000
        r = c.patch(
            "/attendance/EMP9001/2026-07-07",
            json={
                "punch_in": fixed,
                "reason": "clock correction",
                "regularized_by": "hr",
            },
        )
        assert r.status_code == 200 and len(r.json()["history"]) == 1
        assert (
            c.get("/analytics/employees/EMP9001/monthly?month=2026-07").status_code
            == 200
        )
        assert (
            len(
                c.get(
                    "/analytics/departments/QA/trend?from=2026-07-01&to=2026-07-07"
                ).json()["items"]
            )
            == 7
        )


def test_concurrent_punch_in_has_one_winner():
    db.employees.drop()
    db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.post("/employees", json=employee("EMP9002")).status_code == 201
        payload = {"emp_code": "EMP9002", "punched_at": 1783398600000}
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(
                pool.map(
                    lambda _: c.post("/attendance/punch-in", json=payload).status_code,
                    range(6),
                )
            )
        assert statuses.count(201) == 1
        assert statuses.count(409) == 5


def test_validation():
    db.employees.drop()
    db.attendance_logs.drop()
    with TestClient(app) as c:
        assert c.post("/employees", json=employee("bad")).status_code == 422
        assert c.get("/attendance?page=0").status_code == 422
        assert (
            c.get("/attendance?date_from=2026-07-02&date_to=2026-07-01").status_code
            == 422
        )
        assert (
            c.post(
                "/attendance/punch-in", json={"emp_code": "EMP9001", "punched_at": 123}
            ).status_code
            == 422
        )
        assert (
            c.post(
                "/attendance/punch-in",
                json={"emp_code": "EMP9001", "punched_at": 4102444800001},
            ).status_code
            == 422
        )


def test_analytics_ties_joiners_weekends_and_half_up():
    db.employees.drop()
    db.attendance_logs.drop()
    with TestClient(app) as c:
        for code, joined in [
            ("EMP9101", "2026-07-01"),
            ("EMP9102", "2026-07-01"),
            ("EMP9103", "2026-07-03"),
            ("EMP9104", "2026-08-01"),
        ]:
            data = employee(code)
            data["joined_on"] = joined
            assert c.post("/employees", json=data).status_code == 201
        for code, day, hours, late in [
            ("EMP9101", "2026-07-02", 1.00, 10),
            ("EMP9102", "2026-07-02", 1.01, 10),
            ("EMP9103", "2026-07-04", None, 5),
        ]:
            db.attendance_logs.insert_one(
                {
                    "emp_code": code,
                    "date": day,
                    "status": "PRESENT",
                    "punch_in": datetime(2026, 7, 2, tzinfo=timezone.utc),
                    "punch_out": datetime(2026, 7, 2, 1, tzinfo=timezone.utc),
                    "work_hours": hours,
                    "late_minutes": late,
                    "overtime_minutes": 0,
                    "half_day": False,
                    "history": [],
                }
            )
        summary = c.get("/analytics/departments/summary?month=2026-07").json()["items"][
            0
        ]
        assert summary["headcount"] == 3
        assert summary["present_days"] == 2
        assert summary["avg_work_hours"] == 1.01
        rank = c.get("/analytics/leaderboard/late?month=2026-07&limit=1").json()[
            "items"
        ]
        assert [(x["rank"], x["emp_code"]) for x in rank] == [
            (1, "EMP9101"),
            (1, "EMP9102"),
        ]
        trend = c.get(
            "/analytics/departments/QA/trend?from=2026-07-01&to=2026-07-05"
        ).json()["items"]
        assert len(trend) == 5
        assert [x["headcount"] for x in trend] == [2, 2, 3, 3, 3]
        assert trend[3]["attendance_rate"] is None
        assert trend[3]["present_count"] == 1


def test_overnight_and_concurrent_punch_out():
    db.employees.drop()
    db.attendance_logs.drop()
    with TestClient(app) as c:
        assert (
            c.post("/employees", json=employee("EMP9201", "22:00", "06:00")).status_code
            == 201
        )
        pin = int(datetime(2026, 7, 6, 16, 25, tzinfo=timezone.utc).timestamp() * 1000)
        out = int(datetime(2026, 7, 7, 1, 10, tzinfo=timezone.utc).timestamp() * 1000)
        created = c.post(
            "/attendance/punch-in", json={"emp_code": "EMP9201", "punched_at": pin}
        )
        assert created.status_code == 201 and created.json()["date"] == "2026-07-06"
        payload = {"emp_code": "EMP9201", "punched_at": out}
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(
                pool.map(
                    lambda _: c.post("/attendance/punch-out", json=payload).status_code,
                    range(6),
                )
            )
        assert statuses.count(200) == 1 and statuses.count(409) == 5
        row = c.get("/attendance?emp_code=EMP9201").json()["items"][0]
        assert row["work_hours"] == 8.75 and row["overtime_minutes"] == 40
