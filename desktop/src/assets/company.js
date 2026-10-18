import { Call } from "/wails/runtime.js";
const call = (name, ...args) => Call.ByName("main.App." + name, ...args);
const byId = (id) => document.getElementById(id);
const settings = await call("GetSettings");
const en = settings.locale === "en" || settings.Locale === "en";
const localMode = await call("SupportsLocalMode");
byId("use-local").hidden = !localMode;
byId("local-note").hidden = !localMode;
document.documentElement.lang = en ? "en" : "zh";
if (en) {
  const copy = {
    title: "Local file permissions",
    intro:
      "Sign in in the main window, then connect this device and authorize a directory here.",
    "device-title": "Local files and device permissions",
    "pair-note":
      "Sign in to use regular chat. For local files, create a pairing code in My devices, connect here, then authorize a directory.",
    "local-note":
      "Independent local Octop requires an installed or bundled local runtime.",
    "use-local": "Use independent local mode",
    "code-label": "Single-use pairing code",
    pair: "Connect device",
    disconnect: "Disconnect device",
    "folders-title": "Local directory permissions",
    "folders-note":
      "Only directories selected here are accessible. Directory names appear in the company dashboard; sharing file content requires separate consent.",
    "writable-label":
      "Allow creating new files (never overwrite existing files)",
    "company-label":
      "Allow excerpts, file names and tool results to enter the company server and chat history",
    "add-folder": "Choose and authorize directory",
    "results-title": "Local write receipts",
    "results-note":
      "Check final status after disconnect or cancellation. Uncertain does not mean nothing was written.",
    note: "File tools support text, CSV, DOCX and XLSX. Macros and formula recalculation are unavailable. Credentials stay in this app process; pair again after restarting. Signing out of the company account revokes this device.",
  };
  for (const [id, value] of Object.entries(copy)) byId(id).textContent = value;
}
let server = await call("GetCompanyMode");
let busy = false;
const states = en
  ? {
      online: "Connected",
      connecting: "Connecting",
      reconnecting: "Waiting for company server",
      disconnected: "Disconnected — pair again",
      unpaired: "Not connected",
    }
  : {
      online: "设备已连接",
      connecting: "正在连接",
      reconnecting: "等待公司服务恢复",
      disconnected: "连接已断开，请重新配对",
      unpaired: "尚未连接",
    };
async function refresh() {
  try {
    server = await call("GetCompanyMode");
    byId("server").textContent = server
      ? `${en ? "Current server" : "当前服务器"}：${server}`
      : "";
    byId("use-local").disabled = busy;
    byId("device-panel").hidden = !server;
    const status = await call("CompanyStatus");
    byId("status").textContent = states[status.state] || status.state;
    byId("pair").disabled =
      busy ||
      !server ||
      ["online", "connecting", "reconnecting"].includes(status.state);
    byId("disconnect").disabled = busy || status.state === "unpaired";
    byId("add-folder").disabled = busy || status.state !== "online";
    const grants = await call("CompanyGrants");
    byId("folders").replaceChildren(
      ...grants.map((grant) => {
        const row = document.createElement("div");
        row.className = "item";
        const text = document.createElement("span");
        text.textContent = `${grant.name} · ${
          grant.writable
            ? en
              ? "Create files"
              : "可创建"
            : en
            ? "Read only"
            : "只读"
        } · ${
          (grant.allow_company ?? grant.allow_h3c)
            ? en
              ? "Server sharing allowed"
              : "已同意传入服务器"
            : en
            ? "Server sharing consent required"
            : "未同意传入服务器"
        }`;
        const button = document.createElement("button");
        button.className = "secondary";
        button.textContent = en ? "Revoke" : "撤销";
        button.onclick = () =>
          action(() => call("RevokeCompanyDirectory", grant.id));
        row.append(text, button);
        return row;
      }),
    );
    const results = await call("CompanyResults");
    byId("results").replaceChildren(
      ...results.map((result) => {
        const row = document.createElement("div");
        row.className = "item";
        const text = document.createElement("span");
        text.textContent = `${result.path || result.operation_id} · ${
          result.status === "completed"
            ? en
              ? "Saved on this computer"
              : "已保存到这台电脑"
            : result.status === "uncertain"
            ? en
              ? "Check output"
              : "待核对"
            : result.error || result.status
        }`;
        row.append(text);
        if (result.kind === "local_file") {
          const button = document.createElement("button");
          button.className = "secondary";
          button.textContent = en ? "Show in folder" : "在文件夹中显示";
          button.onclick = () =>
            action(() => call("RevealCompanyResult", result.operation_id));
          row.append(button);
        }
        return row;
      }),
    );
  } catch {
    byId("error").textContent = en
      ? "Native device panel unavailable"
      : "原生设备面板不可用";
  }
}
async function action(fn) {
  if (busy) return;
  busy = true;
  byId("error").textContent = "";
  await refresh();
  try {
    await fn();
  } catch (error) {
    byId("error").textContent = String(error);
  } finally {
    busy = false;
    await refresh();
  }
}
byId("use-local").onclick = () =>
  action(async () => {
    await call("UseLocalMode");
  });
byId("pair").onclick = () =>
  action(async () => {
    const code = byId("code").value.trim();
    byId("code").value = "";
    await call("PairCompany", code);
  });
byId("disconnect").onclick = () => action(() => call("DisconnectCompany"));
byId("add-folder").onclick = () =>
  action(async () => {
    await call(
      "AddCompanyDirectory",
      byId("writable").checked,
      byId("allow-company").checked,
    );
    byId("writable").checked = false;
    byId("allow-company").checked = false;
  });
await refresh();
setInterval(refresh, 2000);
