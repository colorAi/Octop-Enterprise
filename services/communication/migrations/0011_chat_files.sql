-- Extend private attachments with downloadable files; existing image rows remain valid.
ALTER TABLE media_assets DROP CONSTRAINT media_assets_purpose_check;
ALTER TABLE media_assets ADD CONSTRAINT media_assets_purpose_check CHECK (purpose IN ('chat_image','chat_file'));
ALTER TABLE media_assets ADD COLUMN filename text NOT NULL DEFAULT '';
ALTER TABLE upload_operations DROP CONSTRAINT upload_operations_purpose_check;
ALTER TABLE upload_operations ADD CONSTRAINT upload_operations_purpose_check CHECK (purpose IN ('chat_image','chat_file'));
ALTER TABLE messages DROP CONSTRAINT messages_type_check;
ALTER TABLE messages ADD CONSTRAINT messages_type_check CHECK (type IN ('text','image','file','system'));
