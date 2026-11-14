-- A short lived capability proof covers the complete Python worker topology.
CREATE TABLE group_expert_runtime (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    protocol integer NOT NULL DEFAULT 0,
    valid_until timestamptz NOT NULL DEFAULT now()
);
INSERT INTO group_expert_runtime(singleton) VALUES(true);
CREATE INDEX group_expert_input_reference ON messages(reply_to_message_id) WHERE mention_expert_id IS NOT NULL;

-- Revocation facts are latched in the authority mutation transaction. A quick
-- disable/re-enable or leave/rejoin cannot revive an already submitted call.
-- These triggers do not take conversation locks after message/member row locks.
CREATE FUNCTION group_expert_revoke_call(target text, reason text) RETURNS void LANGUAGE sql AS $$
    INSERT INTO group_expert_outbox(operation_id,kind,target_id,request_digest,payload)
    SELECT 'revoke:'||call_id||':'||reason,'recall',call_id,'revoke:'||call_id||':'||reason,
        jsonb_build_object('call_id',call_id,'reason_code',reason)
    FROM group_expert_calls WHERE call_id=target AND call_state IN ('pending_admission','queued','dispatched','awaiting_delivery')
    ON CONFLICT(operation_id) DO NOTHING;
$$;
CREATE FUNCTION group_expert_authority_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item record;
BEGIN
    IF TG_TABLE_NAME='messages' THEN
        IF NEW.recalled_at IS NOT NULL AND OLD.recalled_at IS NULL THEN
            FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN messages m ON m.id=c.message_id WHERE m.id=NEW.id OR m.reply_to_message_id=NEW.id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_input_unavailable');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='users' THEN
        IF NEW.status<>'active' OR NOT NEW.permissions ? 'messaging' THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE caller_id=NEW.id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='member_epochs' THEN
        IF TG_OP='INSERT' THEN
            FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN messages m ON m.id=c.message_id JOIN messages quote ON quote.id=m.reply_to_message_id WHERE c.conversation_id=NEW.conversation_id AND quote.seq<NEW.visible_from_seq LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_quote_audience');
            END LOOP;
        ELSIF NEW.left_at IS NOT NULL OR (NEW.role<>'owner' AND NEW.mute_until>now()) OR (NEW.role='member' AND EXISTS(SELECT 1 FROM group_settings WHERE conversation_id=NEW.conversation_id AND mute_all)) THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE conversation_id=NEW.conversation_id AND caller_id=NEW.user_id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='conversations' THEN
        IF NEW.status<>'active' THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE conversation_id=NEW.id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='group_settings' AND NEW.mute_all THEN
        FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN member_epochs m ON m.conversation_id=c.conversation_id AND m.user_id=c.caller_id AND m.left_at IS NULL AND m.role='member' WHERE c.conversation_id=NEW.conversation_id LOOP
            PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER group_expert_message_recall AFTER UPDATE OF recalled_at ON messages FOR EACH ROW EXECUTE FUNCTION group_expert_authority_changed();
CREATE TRIGGER group_expert_user_revocation AFTER UPDATE OF status,permissions ON users FOR EACH ROW EXECUTE FUNCTION group_expert_authority_changed();
CREATE TRIGGER group_expert_membership_revocation AFTER INSERT OR UPDATE OF left_at,mute_until,role ON member_epochs FOR EACH ROW EXECUTE FUNCTION group_expert_authority_changed();
CREATE TRIGGER group_expert_archive AFTER UPDATE OF status ON conversations FOR EACH ROW EXECUTE FUNCTION group_expert_authority_changed();
CREATE TRIGGER group_expert_mute AFTER UPDATE OF mute_all ON group_settings FOR EACH ROW EXECUTE FUNCTION group_expert_authority_changed();
