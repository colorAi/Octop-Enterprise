package store_test

import (
	"context"
	"testing"

	"octop/communication/internal/testdb"
)

func TestJobLeaseMigrationPreservesExistingWork(t *testing.T) {
	st := testdb.Open(t, testdb.New(t))
	ctx := context.Background()
	_, err := st.Pool.Exec(ctx, `ALTER TABLE background_jobs DROP COLUMN lease_token;
        ALTER TABLE delivery_outbox DROP COLUMN lease_token;
        DELETE FROM schema_migrations WHERE version=14;
        INSERT INTO background_jobs(id,kind,work_key,state,attempts,instance) VALUES('job','sync.purge','sync.purge','succeeded',4,'old');
        INSERT INTO delivery_outbox(id,kind,recipient_user_id,work_key,state,attempts,instance,lease_until) VALUES('delivery','sync_wakeup','recipient','delivery','leased',2,'old',now()+interval '1 minute')`)
	if err != nil {
		t.Fatal(err)
	}
	if st.CheckShape(ctx) == nil {
		t.Fatal("old schema accepted")
	}
	versions, err := st.Migrate(ctx)
	if err != nil || len(versions) != 1 || versions[0] != 14 {
		t.Fatalf("migration: %v %v", versions, err)
	}
	if err := st.CheckShape(ctx); err != nil {
		t.Fatal(err)
	}
	var valid bool
	if err := st.Pool.QueryRow(ctx, `SELECT state='succeeded' AND attempts=4 AND instance='old' AND lease_token='' FROM background_jobs WHERE id='job'`).Scan(&valid); err != nil || !valid {
		t.Fatalf("job changed: %v %v", valid, err)
	}
	if err := st.Pool.QueryRow(ctx, `SELECT state='leased' AND attempts=2 AND instance='old' AND lease_token='' AND lease_until IS NOT NULL FROM delivery_outbox WHERE id='delivery'`).Scan(&valid); err != nil || !valid {
		t.Fatalf("delivery changed: %v %v", valid, err)
	}
	versions, err = st.Migrate(ctx)
	if err != nil || len(versions) != 0 {
		t.Fatalf("migration replay: %v %v", versions, err)
	}
}
