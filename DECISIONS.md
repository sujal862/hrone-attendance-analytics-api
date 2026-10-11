# Design decisions

1. **Indexes.** `employees.emp_code` and `(attendance_logs.emp_code, date)` are unique: they enforce identity and make natural-key lookup cheap. `(date desc, emp_code)` serves default attendance order and month analytics; `(status, date desc, emp_code)` serves status-filtered lists. Employee `(department, joined_on)` and `(joined_on, department)` indexes support department/headcount entry points. I rejected indexing derived metrics alone because the month is the useful leading selector and extra indexes slow writes.

2. **Punch-in race.** Both requests may read the employee and build the same record, but each attempts one insert. MongoDB's unique natural-key index accepts exactly one; its duplicate-key error becomes 409 for every loser. There is no check-then-insert race.

3. **Ties.** MongoDB `$rank` creates competition ranks. If two people tie at the cutoff, both are returned because filtering happens on `rank <= limit`; the following rank is skipped.

4. **Headcount.** Department summary starts from eligible employees, then looks up logs while preserving empty arrays. Employees with no records therefore enter the department group.

5. **100x scale.** I would pre-aggregate immutable daily/monthly facts, updated through a change stream, while retaining raw logs as the audit source. This reduces repeated cross-collection work without weakening write correctness.
