# Starter code review

| # | Where | Defect and symptom | Fix |
|---|---|---|---|
| 1 | Client / health | Naive datetimes and a health response that ignored database availability. | Enabled timezone-aware BSON and ping MongoDB, returning 503 on failure. |
| 2 | Startup | No required indexes: duplicates and scans were possible. | Added idempotent unique and query indexes in application lifespan. |
| 3 | Models | Codes, email, dates, shifts, statuses, pagination and epoch values lacked contract validation. | Added constrained Pydantic models and query parameters. |
| 4 | Late helper | Mixed UTC/naive time with IST and failed overnight/boundary cases. | Construct the IST shift instant and apply the strict 10-minute rule. |
| 5 | Work helper | Python float/banker's rounding violated half-up rounding. | Use integer seconds and `Decimal` half-up. |
| 6 | Overtime helper | Ignored the 30-minute threshold, IST and overnight end date. | Use the correct shift-end instant, floor minutes and threshold. |
| 7 | Employee create | Racy check-then-insert and wrong datetime response. | Rely on unique insert, map duplicate-key to 409, return epoch ms. |
| 8 | Employee list | Wrong skip, unfiltered total and no sort. | Correct offset/count and sort by `emp_code`. |
| 9 | Punch-in | Unknown employees caused 500; date/time, truncation and race handling were wrong; internal ID leaked. | Add 404, R1/R2 handling, unique insert and contract response. |
| 10 | Attendance list | Loaded/sorted the full collection in Python, leaked `_id`, and lacked validation. | Use indexed server filtering, sorting and pagination. |
| 11 | Missing endpoints | Punch-out, regularization, analytics and explain were TODOs. | Implement all contract endpoints and pipelines. |
| 12 | Concurrency/audit | No conditional writes protected punch-out or corrections. | Add atomic punch-out and optimistic append-only correction updates. |
| 13 | Legacy data | Missing `history`/`half_day` were not normalized. | Treat them as empty/false in responses and analytics. |

I retained `load_dotenv()` because it does not override real environment variables by default. I also retained synchronous PyMongo: FastAPI runs ordinary endpoint functions in a worker pool, which is adequate for this scoped service.

## Final contract audit

The first implementation needed three further corrections. MongoDB `$round` uses ties-to-even, so department work-hour averages and trend rates could disagree with R8 at exact half-way values; these pipelines now use decimal half-up rounding. A punch-out racing with a correction could have written derived values based on the previous `punch_in`; its atomic update now checks that the punch-in has not changed. Finally, the timestamp model accepted values beyond the OpenAPI maximum; it now enforces the exact contract limit. The added tests cover these boundaries along with overnight punch-out concurrency, joiners, weekend rows, empty days, and leaderboard ties.
