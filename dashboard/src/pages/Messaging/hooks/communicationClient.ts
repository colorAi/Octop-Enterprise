import {
  communicationApi,
  communicationError,
  type CommunicationError,
  uploadCommunicationImage,
  uploadCommunicationFile,
} from "../../../api/modules/communication";
import type {
  CommunicationMessage,
  CommunicationAttachment,
  CommunicationUser,
  Conversation,
  Department,
  SendMessage,
  SyncEvent,
} from "../../../api/types/communication";
import {
  connectCommunicationRealtime,
  type RealtimeStatus,
} from "../../../api/communicationRealtime";
import { getAuthToken } from "../../../api/request";
import { tokenSessionIdentity } from "../../../utils/authSession";
import {
  advanceConversationRead,
  compareSequence,
  mergeMessages,
  snapshotState,
} from "./messagingState";

export interface PendingMessage {
  body: SendMessage;
  conversation_id: string;
  created_at: string;
  status: "sending" | "failed";
  error?: CommunicationError;
}
export interface MessagingState {
  phase: "idle" | "loading" | "ready" | "error" | "forbidden";
  realtime: RealtimeStatus;
  error: CommunicationError | null;
  profile: CommunicationUser | null;
  contacts: Record<string, CommunicationUser>;
  presence: Record<string, true> | null;
  assets: Record<string, CommunicationAttachment>;
  departments: Department[];
  directoryName: string;
  conversations: Record<string, Conversation>;
  histories: Record<string, CommunicationMessage[]>;
  historyPages: Record<
    string,
    { before: string; more: boolean; head?: string }
  >;
  pending: Record<string, PendingMessage>;
  sequence: string;
}
export const emptyMessagingState = (): MessagingState => ({
  phase: "idle",
  realtime: "offline",
  error: null,
  profile: null,
  contacts: {},
  presence: null,
  assets: {},
  departments: [],
  directoryName: "",
  conversations: {},
  histories: {},
  historyPages: {},
  pending: {},
  sequence: "0",
});
export function authSessionIdentity(): string {
  return tokenSessionIdentity(getAuthToken());
}

/** One in-memory client per authenticated account/session; all sync pulls are serialized. */
export class CommunicationClient {
  state = emptyMessagingState();
  readonly scrollPositions = new Map<string, number>();
  private listeners = new Set<() => void>();
  private controller = new AbortController();
  private cursor = "";
  private dirty = false;
  private work: Promise<void> | null = null;
  private focused: string | null = null;
  private poll: ReturnType<typeof setInterval> | undefined;
  private socketStop: (() => void) | undefined;
  private visibility = 0;
  private histories = new Map<
    string,
    { promise: Promise<void>; controller: AbortController; refresh?: boolean }
  >();
  private readonly identity: string;
  private readonly hasContacts: boolean;
  private readonly hasMessaging: boolean;
  private readonly rejected: () => void;
  constructor(
    identity: string,
    hasContacts: boolean,
    hasMessaging: boolean,
    rejected: () => void = () => undefined,
  ) {
    this.identity = identity;
    this.hasContacts = hasContacts;
    this.hasMessaging = hasMessaging;
    this.rejected = rejected;
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.state;
  private valid() {
    return (
      !this.controller.signal.aborted && authSessionIdentity() === this.identity
    );
  }
  private update(value: Partial<MessagingState>) {
    if (!this.valid()) return;
    this.state = { ...this.state, ...value };
    for (const listener of this.listeners) listener();
  }
  start() {
    if (this.controller.signal.aborted) {
      this.controller = new AbortController();
      this.cursor = "";
      this.work = null;
      this.socketStop = undefined;
      this.histories.clear();
    }
    this.update({ phase: "loading" });
    void this.synchronize();
    this.poll = setInterval(() => void this.synchronize(), 30_000);
    document.addEventListener("visibilitychange", this.visible);
  }
  stop() {
    this.controller.abort();
    this.socketStop?.();
    if (this.poll) clearInterval(this.poll);
    document.removeEventListener("visibilitychange", this.visible);
    for (const job of this.histories.values()) job.controller.abort();
    this.state = emptyMessagingState();
    this.scrollPositions.clear();
    for (const listener of this.listeners) listener();
  }
  private visible = () => {
    if (document.visibilityState === "visible") void this.synchronize();
  };
  private failure(error: unknown) {
    if (!this.valid()) return;
    const value = communicationError(error);
    if ([401, 403].includes(value.status)) {
      this.update({
        ...emptyMessagingState(),
        phase: "forbidden",
        error: value,
      });
      this.controller.abort();
      this.socketStop?.();
      for (const job of this.histories.values()) job.controller.abort();
      if (this.poll) clearInterval(this.poll);
      this.rejected();
    } else
      this.update({
        phase: this.state.phase === "ready" ? "ready" : "error",
        error: value,
        realtime: "retrying",
        presence: null,
      });
  }
  synchronize = ({ throwOnError = false } = {}): Promise<void> => {
    if (!this.valid())
      return throwOnError
        ? Promise.reject(
            this.state.error ?? new Error("Communication client is not active"),
          )
        : Promise.resolve();
    this.dirty = true;
    if (!this.work)
      this.work = (async () => {
        try {
          while (this.dirty && this.valid()) {
            this.dirty = false;
            if (!this.cursor || this.state.phase !== "ready")
              await this.bootstrap();
            else await this.pull();
            await this.refreshPresence();
          }
        } catch (error) {
          this.failure(error);
          throw error;
        } finally {
          this.work = null;
        }
      })();
    // Polling handles errors through state; user actions can await a real result.
    return throwOnError ? this.work : this.work.catch(() => undefined);
  };
  private async snapshot() {
    const signal = this.controller.signal;
    const snap = await communicationApi.snapshot(signal);
    const events: SyncEvent[] = [];
    // Each immutable snapshot chunk is independent; bound concurrency while
    // retaining chunk order for deterministic state materialization.
    for (let start = 0; start < snap.chunk_count; start += 4) {
      const batch = await Promise.all(
        Array.from(
          { length: Math.min(4, snap.chunk_count - start) },
          (_, offset) =>
            communicationApi.chunk(snap.snapshot_id, start + offset, signal),
        ),
      );
      for (const [offset, value] of batch.entries()) {
        const chunk = start + offset;
        if (
          value.snapshot_id !== snap.snapshot_id ||
          value.chunk_index !== chunk ||
          value.chunk_count !== snap.chunk_count
        )
          throw new Error("Invalid snapshot chunk");
        events.push(...(value.events ?? []));
      }
    }
    if (!this.valid()) return;
    this.visibility++;
    for (const job of this.histories.values()) job.controller.abort();
    const value = snapshotState(events);
    for (const [id, row] of Object.entries(value.conversations))
      value.conversations[id] = advanceConversationRead(
        row,
        this.state.conversations[id]?.last_read_seq ?? "0",
      );
    this.cursor = snap.cursor;
    this.update({
      ...value,
      histories: {},
      historyPages: {},
      sequence: snap.until_seq,
    });
  }
  private async bootstrap() {
    if (this.hasMessaging) await this.snapshot();
    await this.refreshMetadata();
    if (!this.valid()) return;
    this.update({ phase: "ready", error: null });
    if (this.focused) await this.loadHistory(this.focused);
    if ((this.hasMessaging || this.hasContacts) && !this.socketStop)
      this.socketStop = connectCommunicationRealtime(
        this.controller.signal,
        () => void this.synchronize(),
        (status) =>
          this.update({
            realtime: status,
            ...(status === "online" ? {} : { presence: null }),
          }),
        (error) => this.failure(error),
      );
    if (this.hasMessaging) await this.pull();
  }
  private async refreshPresence() {
    if (!this.hasContacts) return;
    try {
      const value = await communicationApi.presence(this.controller.signal);
      this.update({
        presence: Object.fromEntries(
          value.online_user_ids.map((id) => [id, true as const]),
        ),
      });
    } catch (error) {
      this.update({ presence: null });
      if ([401, 403].includes(communicationError(error).status)) throw error;
    }
  }
  private async refreshMetadata() {
    const signal = this.controller.signal;
    const conversations = async () => {
      if (this.hasMessaging) {
        const rows: Conversation[] = [];
        let cursor: string | undefined;
        do {
          const page = await communicationApi.conversations(cursor, signal);
          rows.push(...page.items);
          if (page.has_more && !page.next_cursor)
            throw new Error("Missing conversation cursor");
          cursor = page.has_more ? page.next_cursor : undefined;
        } while (cursor && this.valid());
        if (!this.valid()) return;
        const conversations = { ...this.state.conversations };
        for (const row of rows)
          conversations[row.conversation_id] = advanceConversationRead(
            { ...conversations[row.conversation_id], ...row },
            conversations[row.conversation_id]?.last_read_seq ?? "0",
          );
        this.update({ conversations });
      }
    };
    const directory = async () => {
      if (this.hasContacts) {
        const [contacts, depts, settings] = await Promise.all([
          communicationApi.contacts(signal),
          communicationApi.departments(signal),
          communicationApi.directorySettings(signal),
        ]);
        if (this.valid())
          this.update({
            contacts: Object.fromEntries(
              contacts.items.map((user) => [user.user_id, user]),
            ),
            departments: depts.items,
            directoryName: settings.name,
          });
      }
    };
    await Promise.all([conversations(), directory()]);
  }
  private async pull() {
    if (!this.hasMessaging) {
      await this.refreshMetadata();
      return;
    }
    let resnapshot = false,
      changed = false;
    const affected = new Set<string>();
    const expertMessages = new Map<string, Set<string>>();
    try {
      for (;;) {
        const page = await communicationApi.sync(
          this.cursor,
          this.controller.signal,
        );
        if (!this.valid()) return;
        let sequence = this.state.sequence;
        for (const event of page.events) {
          if (compareSequence(event.seq, this.state.sequence) <= 0) continue;
          changed = true;
          if (
            [
              "group.members.changed",
              "contacts.changed",
              "profile.updated",
              "group.update",
              "conversation.created",
              "conversation.hidden",
            ].includes(event.type)
          )
            resnapshot = true;
          const conv = event.payload.conversation_id;
          if (
            event.type === "conversation.state.changed" &&
            typeof conv === "string" &&
            typeof event.payload.last_read_seq === "string" &&
            this.state.conversations[conv]
          ) {
            this.update({
              conversations: {
                ...this.state.conversations,
                [conv]: advanceConversationRead(
                  this.state.conversations[conv],
                  event.payload.last_read_seq,
                ),
              },
            });
          }
          if (typeof conv === "string") {
            if (event.type === "group.expert.call") {
              const ids = expertMessages.get(conv) ?? new Set<string>();
              ids.add(
                typeof event.payload.message_id === "string"
                  ? event.payload.message_id
                  : event.object_id,
              );
              expertMessages.set(conv, ids);
            } else affected.add(conv);
          }
          if (event.type === "message.recall") {
            const histories = { ...this.state.histories };
            for (const [id, items] of Object.entries(histories))
              histories[id] = items.map((message) =>
                message.message_id === event.object_id
                  ? { ...message, recalled: true, text: null, attachments: [] }
                  : message.reply_preview?.message_id === event.object_id
                  ? {
                      ...message,
                      reply_preview: {
                        ...message.reply_preview,
                        recalled: true,
                        text: null,
                        attachments: [],
                      },
                    }
                  : message,
              );
            this.update({ histories });
          }
          if (event.type === "conversation.cleared") {
            this.visibility++;
            const id = String(conv);
            this.histories.get(id)?.controller.abort();
            this.update({
              histories: { ...this.state.histories, [id]: [] },
              historyPages: {
                ...this.state.historyPages,
                [id]: { before: "0", more: false },
              },
            });
          }
          sequence = event.seq;
        }
        // Retain the continuation only after the corresponding state updates have been materialized.
        if (resnapshot) {
          await this.snapshot();
          await this.refreshMetadata();
          if (this.focused) await this.loadHistory(this.focused);
          return;
        }
        if (changed) await this.refreshMetadata();
        if (this.focused && affected.has(this.focused))
          await this.loadHistory(this.focused);
        for (const [conv, ids] of expertMessages)
          await this.refreshExpertMessages(conv, ids);
        if (!this.valid()) return;
        this.cursor = page.cursor;
        this.update({ error: null, sequence });
        if (!page.has_more) break;
        changed = false;
        affected.clear();
        expertMessages.clear();
      }
    } catch (error) {
      if (communicationError(error).code === "sync_cursor_expired") {
        this.cursor = "";
        await this.bootstrap();
        return;
      }
      throw error;
    }
  }
  private refreshExpertMessages(conv: string, ids: Set<string>): Promise<void> {
    // Serialize with history reads so late page responses cannot overwrite the
    // event's state. Unloaded messages get current state when paged into view.
    if (!this.valid()) return Promise.resolve();
    const existing = this.histories.get(conv);
    if (existing)
      return existing.promise.then(() => this.refreshExpertMessages(conv, ids));
    const revision = this.visibility;
    const cached = (this.state.histories[conv] ?? []).filter((message) =>
      ids.has(message.message_id),
    );
    if (!cached.length) return Promise.resolve();
    const controller = new AbortController();
    const abort = () => controller.abort();
    this.controller.signal.addEventListener("abort", abort, { once: true });
    const task = (async () => {
      try {
        for (let start = 0; start < cached.length; start += 4) {
          const updates = new Map<string, CommunicationMessage | null>(
            await Promise.all(
              cached.slice(start, start + 4).map(async ({ message_id }) => {
                try {
                  const message = await communicationApi.message(
                    conv,
                    message_id,
                    controller.signal,
                  );
                  return [message_id, message] as const;
                } catch (error) {
                  // A clear on another device can take effect before its sync
                  // event reaches this page. Keep pulling past the stale event.
                  if (communicationError(error).status !== 404) throw error;
                  return [message_id, null] as const;
                }
              }),
            ),
          );
          if (
            controller.signal.aborted ||
            !this.valid() ||
            this.visibility !== revision
          )
            return;
          // Keep quote and mention fields from history. The single-message
          // endpoint supplies the call state and can confirm a recall.
          this.update({
            histories: {
              ...this.state.histories,
              [conv]: (this.state.histories[conv] ?? []).flatMap((message) => {
                const current = updates.get(message.message_id);
                if (current === null) return [];
                if (!current) return [message];
                const updated = {
                  ...message,
                  expert_call: current.expert_call ?? message.expert_call,
                };
                if (current.recalled)
                  return [
                    { ...updated, recalled: true, text: null, attachments: [] },
                  ];
                return [updated];
              }),
            },
          });
        }
      } catch (error) {
        if (!controller.signal.aborted) throw error;
      } finally {
        this.controller.signal.removeEventListener("abort", abort);
        if (this.histories.get(conv)?.controller === controller)
          this.histories.delete(conv);
      }
    })();
    this.histories.set(conv, { promise: task, controller, refresh: true });
    return task;
  }
  focus(conv: string | null) {
    this.focused = conv;
    if (conv)
      void this.loadHistory(conv).catch((error) => {
        if (communicationError(error).status === 403) void this.synchronize();
        else this.failure(error);
      });
  }
  loadHistory(conv: string, older = false): Promise<void> {
    if (!this.valid()) return Promise.resolve();
    const existing = this.histories.get(conv);
    if (existing)
      return older && !existing.refresh
        ? existing.promise
        : existing.promise.then(() => this.loadHistory(conv, older));
    const rev = this.visibility;
    const controller = new AbortController();
    const abort = () => controller.abort();
    this.controller.signal.addEventListener("abort", abort, { once: true });
    const task = (async () => {
      try {
        const before = older
          ? this.state.historyPages[conv]?.before
          : undefined;
        let page = await communicationApi.history(
          conv,
          before,
          controller.signal,
        );
        if (!this.valid() || rev !== this.visibility) return;
        const latest =
          page.items[0]?.seq ?? this.state.historyPages[conv]?.head ?? "0";
        const incoming = [...page.items];
        const anchor = this.state.historyPages[conv]?.head;
        // Bridge a reconnect gap before merging the latest page into older cached history.
        while (
          !older &&
          anchor &&
          page.has_more &&
          page.items.length &&
          compareSequence(page.items[page.items.length - 1].seq, anchor) > 0
        ) {
          page = await communicationApi.history(
            conv,
            page.next_before_seq,
            controller.signal,
          );
          incoming.push(...page.items);
        }
        if (!this.valid() || rev !== this.visibility) return;
        const messages = mergeMessages(
          older ? this.state.histories[conv] ?? [] : [],
          incoming,
        );
        const pending = { ...this.state.pending };
        for (const message of messages)
          if (
            message.client_message_id &&
            message.sender_id === this.state.profile?.user_id
          )
            delete pending[message.client_message_id];
        const pages = { ...this.state.historyPages };
        if (older || !pages[conv])
          pages[conv] = {
            before: page.next_before_seq,
            more: page.has_more,
            head: older ? pages[conv]?.head ?? latest : latest,
          };
        if (!older && pages[conv])
          pages[conv] = { ...pages[conv], head: latest };
        this.update({
          histories: {
            ...this.state.histories,
            [conv]: older
              ? messages
              : mergeMessages(this.state.histories[conv] ?? [], messages),
          },
          historyPages: pages,
          pending,
        });
      } catch (error) {
        if (!controller.signal.aborted) throw error;
      } finally {
        this.controller.signal.removeEventListener("abort", abort);
        if (this.histories.get(conv)?.controller === controller)
          this.histories.delete(conv);
        if (this.valid() && rev !== this.visibility && this.focused === conv)
          void this.loadHistory(conv).catch((error) => this.failure(error));
      }
    })();
    this.histories.set(conv, { promise: task, controller });
    return task;
  }
  async direct(user: string) {
    if (!this.valid()) throw new Error("Communication client is not active");
    const existing = Object.values(this.state.conversations).find(
      (row) =>
        row.type === "private" &&
        row.peer_user_id === user &&
        !row.hidden_at_seq &&
        !row.archived &&
        row.can_send !== false,
    );
    if (existing) return existing.conversation_id;
    const value = await communicationApi.direct(user, this.controller.signal);
    if (!this.valid()) throw new Error("Communication client is not active");
    const conversation = this.state.conversations[value.conversation_id] ?? {
      conversation_id: value.conversation_id,
      type: "private" as const,
      peer_user_id: user,
      last_seq: "0",
      pinned: false,
      muted: false,
    };
    this.update({
      conversations: {
        ...this.state.conversations,
        [value.conversation_id]: {
          ...conversation,
          can_send: true,
          hidden_at_seq: null,
          archived: false,
        },
      },
    });
    // The server has authorized/opened the conversation. Show it immediately;
    // history and authoritative metadata continue independently in the UI.
    void this.synchronize();
    return value.conversation_id;
  }
  async send(conv: string, body: SendMessage) {
    const visibility = this.visibility;
    const pending: PendingMessage = {
      body,
      conversation_id: conv,
      created_at:
        this.state.pending[body.client_message_id]?.created_at ??
        new Date().toISOString(),
      status: "sending",
    };
    this.update({
      pending: { ...this.state.pending, [body.client_message_id]: pending },
    });
    try {
      const receipt = await communicationApi.send(
        conv,
        body,
        this.controller.signal,
      );
      if (!this.valid()) return;
      const message: CommunicationMessage = {
        message_id: receipt.message_id,
        seq: receipt.seq,
        sender_id: this.state.profile?.user_id ?? "",
        type: body.asset_ids?.some(
          (id) => this.state.assets[id]?.purpose === "chat_file",
        )
          ? "file"
          : body.asset_ids?.length
          ? "image"
          : "text",
        text: body.text ?? null,
        created_at: receipt.created_at,
        recalled: false,
        client_message_id: body.client_message_id,
        mention_user_ids: body.mention_user_ids,
        mention_all: body.mention_all,
        mention_expert_id: body.mention_expert_id,
        expert_call: receipt.expert_call,
        attachments: body.asset_ids?.map(
          (asset_id) =>
            this.state.assets[asset_id] ?? {
              asset_id,
              state: "ready",
            },
        ),
      };
      const remaining = { ...this.state.pending };
      delete remaining[body.client_message_id];
      // Clears/snapshots may overtake a send; a replay can predate them as well.
      // Only authorized history may restore content across those boundaries.
      const reload = visibility !== this.visibility || receipt.replayed;
      this.update({
        pending: remaining,
        ...(!reload
          ? {
              histories: {
                ...this.state.histories,
                [conv]: mergeMessages(this.state.histories[conv] ?? [], [
                  message,
                ]),
              },
            }
          : {}),
      });
      if (reload)
        await this.loadHistory(conv).catch((error) => this.failure(error));
      void this.synchronize();
    } catch (error) {
      if (!this.valid()) return;
      const value = communicationError(error);
      if (value.code === "account_disabled" || value.status === 401) {
        this.failure(error);
        return;
      }
      this.update({
        pending: {
          ...this.state.pending,
          [body.client_message_id]: {
            ...pending,
            status: "failed",
            error: value,
          },
        },
      });
      throw value;
    }
  }
  image(file: File) {
    return this.upload(file, "chat_image");
  }
  file(file: File) {
    return this.upload(file, "chat_file");
  }
  private async upload(file: File, purpose: "chat_image" | "chat_file") {
    const signal = this.controller.signal;
    const id = await (purpose === "chat_image"
      ? uploadCommunicationImage
      : uploadCommunicationFile)(file, signal);
    signal.throwIfAborted();
    this.update({
      assets: {
        ...this.state.assets,
        [id]: {
          asset_id: id,
          state: "ready",
          purpose,
          filename: file.name,
          mime: file.type,
          size_bytes: file.size,
        },
      },
    });
    return id;
  }
  async read(conv: string, seq: string) {
    if (!this.valid()) return;
    const current = this.state.conversations[conv]?.last_read_seq ?? "0";
    if (compareSequence(seq, current) <= 0) return;
    const value = await communicationApi.read(
      conv,
      seq,
      this.controller.signal,
    );
    if (!this.valid()) return;
    const row = this.state.conversations[conv];
    if (row)
      this.update({
        conversations: {
          ...this.state.conversations,
          [conv]: advanceConversationRead(row, value.last_read_seq),
        },
      });
  }
  async mutate(action: () => Promise<unknown>) {
    await action();
    await this.synchronize({ throwOnError: true });
  }
}
