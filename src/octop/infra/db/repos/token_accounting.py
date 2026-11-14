"""Quota usage combines execution receipts and usage logs without double charging."""

from typing import Any


def quota_tokens(conn: Any, user: int) -> int:
    legacy = conn.execute(
        "SELECT COALESCE(SUM(total_tokens),0) FROM usage_log l WHERE user_id=? "
        "AND (execution_run_id IS NULL OR NOT EXISTS "
        "(SELECT 1 FROM execution_runs r WHERE r.run_id=l.execution_run_id))",
        (user,),
    ).fetchone()[0]
    charged = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN COALESCE(s.total,0)>COALESCE(l.total,0) "
        "THEN s.total ELSE COALESCE(l.total,0) END),0) FROM execution_runs r "
        "LEFT JOIN (SELECT s.run_id,SUM(CASE WHEN s.state='settled' THEN s.actual "
        "WHEN s.state='unknown' AND r.operation IN ('desktop','group_expert') THEN COALESCE(s.estimated,s.reserved) "
        "ELSE 0 END) total FROM execution_reservations s JOIN execution_runs r "
        "ON r.run_id=s.run_id WHERE r.user_id=? GROUP BY s.run_id) s ON s.run_id=r.run_id "
        "LEFT JOIN (SELECT execution_run_id,SUM(total_tokens) total FROM usage_log "
        "WHERE user_id=? AND execution_run_id IS NOT NULL GROUP BY execution_run_id) l "
        "ON l.execution_run_id=r.run_id WHERE r.user_id=?",
        (user, user, user),
    ).fetchone()[0]
    return int(legacy + charged)
