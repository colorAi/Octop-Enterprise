package store_test

import (
	"context"
	"octop/communication/internal/testdb"
	"testing"
)

func TestUpgradeChatFilesPreservesImages(t *testing.T) {
	st := testdb.Open(t, testdb.New(t))
	ctx := context.Background()
	_, err := st.Pool.Exec(ctx, `
 ALTER TABLE media_assets DROP CONSTRAINT media_assets_purpose_check;
 ALTER TABLE media_assets ADD CONSTRAINT media_assets_purpose_check CHECK (purpose IN ('chat_image'));
 ALTER TABLE media_assets DROP COLUMN filename;
 ALTER TABLE upload_operations DROP CONSTRAINT upload_operations_purpose_check;
 ALTER TABLE upload_operations ADD CONSTRAINT upload_operations_purpose_check CHECK (purpose IN ('chat_image'));
 ALTER TABLE messages DROP CONSTRAINT messages_type_check;
 ALTER TABLE messages ADD CONSTRAINT messages_type_check CHECK (type IN ('text','image','system'));
 DELETE FROM schema_migrations WHERE version=11;
 INSERT INTO media_assets(id,owner_id,purpose,state,mime) VALUES('legacy-image','alice','chat_image','ready','image/png');
 `)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.CheckShape(ctx); err == nil {
		t.Fatal("old schema unexpectedly accepted")
	}
	applied, err := st.Migrate(ctx)
	if err != nil || len(applied) != 1 || applied[0] != 11 {
		t.Fatal("upgrade failed", applied, err)
	}
	if err := st.CheckShape(ctx); err != nil {
		t.Fatal(err)
	}
	var purpose, filename string
	if err := st.Pool.QueryRow(ctx, `SELECT purpose,filename FROM media_assets WHERE id='legacy-image'`).Scan(&purpose, &filename); err != nil || purpose != "chat_image" || filename != "" {
		t.Fatal("legacy image changed", err)
	}
}
