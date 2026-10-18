package policy

import (
	"context"
	"github.com/jackc/pgx/v5"
)

type Queryable interface {
	QueryRow(context.Context, string, ...any) pgx.Row
}

func Active(ctx context.Context, q Queryable, user string) bool {
	var ok bool
	return q.QueryRow(ctx, `SELECT status='active' AND permissions ? 'messaging' FROM users WHERE id=$1`, user).Scan(&ok) == nil && ok
}
func CanCommunicate(ctx context.Context, q Queryable, a, b string) bool {
	var ok bool
	return a != b && q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM users u JOIN users v ON v.id=$2
 WHERE u.id=$1 AND u.status='active' AND v.status='active' AND u.permissions ? 'messaging' AND v.permissions ? 'messaging')`, a, b).Scan(&ok) == nil && ok
}
func AreBlocked(ctx context.Context, q Queryable, a, b string) bool {
	var ok bool
	if q.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM user_blocks WHERE blocker_id=$1 AND blocked_id=$2 OR blocker_id=$2 AND blocked_id=$1)`, a, b).Scan(&ok) != nil {
		return true
	}
	return ok
}
