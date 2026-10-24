function mountConnectionPanel(config) {
  if (!document.body || !window._wails?.invoke || !config.address || location.origin !== config.address) return;
  if (location.pathname === "/desktop-login.html") return;
  const emit = name => window._wails.invoke("wails:event:emit:desktop:" + name);
  if (location.pathname === "/login") { emit("show-login"); return; }
  if (window.octopDesktopWorkspace) return;
  // No injected UI. React renders the work directory in the existing settings.
  const pending = new Map();
  let sequence = 0;
  function request(action, code = "") {
    return new Promise((resolve, reject) => {
      const id = String(++sequence);
      const timer = setTimeout(() => { pending.delete(id); reject(new Error("timeout")); }, action === "choose" ? 300000 : 30000);
      pending.set(id, { resolve, reject, timer });
      emit(`workspace:${config.nonce}:${id}:${action}:${encodeURIComponent(code)}`);
    });
  }
  window.__octopWorkspaceReply = (nonce, result) => {
    if (nonce !== config.nonce) return;
    const call = pending.get(result.id);
    if (!call) return;
    pending.delete(result.id);
    clearTimeout(call.timer);
    if (result.error) call.reject(new Error(result.error));
    else call.resolve(result.state);
  };
  window.octopDesktopWorkspace = Object.freeze({
    status: () => request("status"),
    pair: code => request("pair", code),
    choose: () => request("choose"),
    clear: () => request("clear"),
    disconnect: () => request("disconnect"),
  });
  const runtimePending = new Map();
  const runtimeListeners = new Set();
  function runtimeRequest(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = String(++sequence);
      const timer = setTimeout(() => { runtimePending.delete(id); reject(new Error("本地运行时响应超时；请核对任务状态后重试")); }, 60000);
      runtimePending.set(id, { resolve, reject, timer });
      emit(`runtime:${config.nonce}:${id}:${method}:${encodeURIComponent(JSON.stringify(params))}`);
    });
  }
  window.__octopRuntimeReply = (nonce, result) => {
    if (nonce !== config.nonce) return;
    const call = runtimePending.get(result.id);
    if (!call) return;
    runtimePending.delete(result.id); clearTimeout(call.timer);
    if (result.error) call.reject(new Error(result.error)); else call.resolve(result.result);
  };
  window.__octopRuntimeEvent = (nonce, event) => {
    if (nonce === config.nonce) for (const listener of runtimeListeners) listener(event);
  };
  window.__octopRuntime = Object.freeze({
    version: 1,
    status: (options) => runtimeRequest("status", options),
    refreshStatus: () => runtimeRequest("refresh_status"),
    start: params => runtimeRequest("start", params),
    cancel: params => runtimeRequest("cancel", params),
    steer: params => runtimeRequest("steer", params),
    removeSteer: params => runtimeRequest("steer_remove", params),
    resumeHitl: params => runtimeRequest("hitl_resume", params),
    reveal: params => runtimeRequest("reveal", params),
    previewImage: params => runtimeRequest("preview_image", params),
    uploadArtifact: params => runtimeRequest("upload_artifact", params),
    importAttachment: params => runtimeRequest("import_attachment", params),
    reconcile: params => runtimeRequest("reconcile", params),
    subscribe: listener => { runtimeListeners.add(listener); return () => runtimeListeners.delete(listener); },
  });
  window.dispatchEvent(new Event("octop:desktop-workspace-ready"));
  // Prepare verified tools while the employee opens a conversation. This only
  // restores receipts and never starts a model or replays a local operation.
  void runtimeRequest("status").catch(() => {});
  // Logout also stops local grants immediately, including SPA navigation.
  let sent = false;
  const observer = new MutationObserver(() => {
    if (location.pathname === "/login" && !sent) {
      sent = true;
      emit("show-login");
      observer.disconnect();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
