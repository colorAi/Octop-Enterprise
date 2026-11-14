-- Stop legacy per-call rows from behaving as recurring maintenance jobs.
-- Business obligations remain in group_expert_outbox and are polled independently.
UPDATE background_jobs SET state='skipped',lease_until=NULL,lease_token='',finished_at=now(),updated_at=now()
WHERE kind='group.expert.reconcile' AND work_key<>'group.expert.reconcile';
ALTER TABLE group_expert_calls ADD COLUMN max_text_chars integer CHECK(max_text_chars>0);
CREATE INDEX group_expert_calls_active ON group_expert_calls(call_id)
WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery','cancel_requested');
CREATE INDEX group_expert_recall_target ON group_expert_outbox(target_id,created_at,operation_id) WHERE kind='recall';
CREATE OR REPLACE FUNCTION group_expert_authority_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item record;
BEGIN
    IF TG_TABLE_NAME='messages' THEN
        IF NEW.recalled_at IS NOT NULL AND OLD.recalled_at IS NULL THEN
            FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN messages m ON m.id=c.message_id WHERE c.call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND (m.id=NEW.id OR m.reply_to_message_id=NEW.id) LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_input_unavailable');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='users' THEN
        IF NEW.status<>'active' OR NOT NEW.permissions ? 'messaging' THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND caller_id=NEW.id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='member_epochs' THEN
        IF TG_OP='INSERT' THEN
            FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN messages m ON m.id=c.message_id JOIN messages quote ON quote.id=m.reply_to_message_id WHERE c.call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND c.conversation_id=NEW.conversation_id AND quote.seq<NEW.visible_from_seq LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_quote_audience');
            END LOOP;
        ELSIF NEW.left_at IS NOT NULL OR (NEW.role<>'owner' AND NEW.mute_until>now()) OR (NEW.role='member' AND EXISTS(SELECT 1 FROM group_settings WHERE conversation_id=NEW.conversation_id AND mute_all)) THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND conversation_id=NEW.conversation_id AND caller_id=NEW.user_id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='conversations' THEN
        IF NEW.status<>'active' THEN
            FOR item IN SELECT call_id FROM group_expert_calls WHERE call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND conversation_id=NEW.id LOOP
                PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
            END LOOP;
        END IF;
    ELSIF TG_TABLE_NAME='group_settings' AND NEW.mute_all THEN
        FOR item IN SELECT c.call_id FROM group_expert_calls c JOIN member_epochs m ON m.conversation_id=c.conversation_id AND m.user_id=c.caller_id AND m.left_at IS NULL AND m.role='member' WHERE c.call_state IN ('pending_admission','queued','dispatched','awaiting_delivery') AND c.conversation_id=NEW.conversation_id LOOP
            PERFORM group_expert_revoke_call(item.call_id,'group_expert_send_not_allowed');
        END LOOP;
    END IF;
    RETURN NEW;
END;
$$;
