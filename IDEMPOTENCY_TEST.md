# SchemaPilot Idempotency Test

## Goal

Prove that re-running the exact same migration does not duplicate external writes.

The stable `migration_job_id` is derived from:

- source file SHA-256
- target schema SHA-256
- organization master SHA-256
- approved date policy
- workflow version

Row-level idempotency keys are stored in:

```text
schemapilot_agent/.adk/migration_ledger.db
```

Generated files are written under:

```text
migration_output/<migration_job_id>/
```

## Test

1. Start ADK Web:

```powershell
adk web --port 8000
```

2. Run the same `employees.csv` migration and approve with:

```text
APPROVE_DMY
```

3. Record the final:

```text
migration_job_id
idempotent_replay
```

The first run should show:

```text
idempotent_replay: false
```

4. Create a **New Session** and run the exact same source file again with the same `APPROVE_DMY`.

The second run must have the **same** `migration_job_id` and should show:

```text
status: IDEMPOTENT_REPLAY_SKIPPED
idempotent_replay: true
```

at the write step.

The final reconciliation should still be valid and the output CSV must not contain duplicated rows.

## Inspect the ledger

From the project root:

```powershell
python -c "import sqlite3; db=r'schemapilot_agent\\.adk\\migration_ledger.db'; c=sqlite3.connect(db); print('jobs=', c.execute('select job_id,status from migration_jobs').fetchall()); print('rows=', c.execute('select job_id,count(*) from migration_rows group by job_id').fetchall())"
```

For the current 5-row sample, one job should have 5 row-ledger records, not 10 after a replay.
