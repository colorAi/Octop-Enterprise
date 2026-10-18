CREATE TABLE IF NOT EXISTS communication_identities(user_id INTEGER PRIMARY KEY,version BIGINT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS communication_identity_outbox(user_id INTEGER NOT NULL,version BIGINT NOT NULL,payload TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at BIGINT NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,version));
CREATE TABLE IF NOT EXISTS communication_tickets(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL,session_id TEXT NOT NULL,expires_at BIGINT NOT NULL,session_expires_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS communication_revoked_sessions(session_id TEXT PRIMARY KEY,expires_at BIGINT NOT NULL);
CREATE TRIGGER IF NOT EXISTS communication_user_insert AFTER INSERT ON users BEGIN
INSERT INTO communication_identities(user_id,version,payload) VALUES(NEW.id,1,json_object('user_id',NEW.id,'nickname',COALESCE(NEW.display_name,NEW.username),'avatar_icon',COALESCE(NEW.avatar_icon,''),'status',CASE WHEN NEW.disabled=1 THEN 'disabled' ELSE 'active' END,'role',NEW.role,'permissions',json(NEW.permissions))) ON CONFLICT(user_id) DO UPDATE SET version=communication_identities.version+1,payload=excluded.payload;
INSERT INTO communication_identity_outbox(user_id,version,payload) SELECT user_id,version,payload FROM communication_identities WHERE user_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS communication_user_update AFTER UPDATE OF display_name,username,avatar_icon,disabled,role,permissions ON users
WHEN OLD.display_name IS NOT NEW.display_name OR OLD.username IS NOT NEW.username OR OLD.avatar_icon IS NOT NEW.avatar_icon OR OLD.disabled IS NOT NEW.disabled OR OLD.role IS NOT NEW.role OR OLD.permissions IS NOT NEW.permissions
BEGIN
INSERT INTO communication_identities(user_id,version,payload) VALUES(NEW.id,1,json_object('user_id',NEW.id,'nickname',COALESCE(NEW.display_name,NEW.username),'avatar_icon',COALESCE(NEW.avatar_icon,''),'status',CASE WHEN NEW.disabled=1 THEN 'disabled' ELSE 'active' END,'role',NEW.role,'permissions',json(NEW.permissions))) ON CONFLICT(user_id) DO UPDATE SET version=communication_identities.version+1,payload=excluded.payload;
INSERT INTO communication_identity_outbox(user_id,version,payload) SELECT user_id,version,payload FROM communication_identities WHERE user_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS communication_user_delete BEFORE DELETE ON users BEGIN
INSERT INTO communication_identities(user_id,version,payload) VALUES(OLD.id,1,json_object('user_id',OLD.id,'status','deleted','role',OLD.role,'nickname',COALESCE(OLD.display_name,OLD.username),'avatar_icon','','permissions',json('[]'))) ON CONFLICT(user_id) DO UPDATE SET version=communication_identities.version+1,payload=excluded.payload;
INSERT INTO communication_identity_outbox(user_id,version,payload) SELECT user_id,version,payload FROM communication_identities WHERE user_id=OLD.id;
END;
INSERT OR IGNORE INTO communication_identities SELECT id,1,json_object('user_id',id,'nickname',COALESCE(display_name,username),'avatar_icon',COALESCE(avatar_icon,''),'status',CASE WHEN disabled=1 THEN 'disabled' ELSE 'active' END,'role',role,'permissions',json(permissions)) FROM users;
INSERT OR IGNORE INTO communication_identity_outbox(user_id,version,payload) SELECT user_id,version,payload FROM communication_identities;
UPDATE _schema_version SET version=21;
