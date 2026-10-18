import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";

// Reuse the repository's installed dashboard test dependencies.
const require = createRequire(
  new URL("../../dashboard/package.json", import.meta.url),
);
const { JSDOM } = require("jsdom");
const html = await readFile(
  new URL("../src/assets/company.html", import.meta.url),
  "utf8",
);
const script = (
  await readFile(new URL("../src/assets/company.js", import.meta.url), "utf8")
).replace(
  'import { Call } from "/wails/runtime.js";',
  "const Call = window.nativeCall;",
);

async function panel(server = "", localMode = true) {
  const dom = new JSDOM(html, { runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  let rejectConnection = false;
  window.setInterval = () => 1;
  window.nativeCall = {
    ByName: async (method, ...args) => {
      const name = method.replace("main.App.", "");
      calls.push([name, ...args]);
      switch (name) {
        case "SupportsLocalMode":
          return localMode;
        case "GetSettings":
          return { locale: "zh" };
        case "GetCompanyMode":
          return server;
        case "CompanyStatus":
          return { state: "unpaired" };
        case "CompanyGrants":
        case "CompanyResults":
          return [];
        case "ConfigureCompany":
          if (rejectConnection) throw new Error("TLS certificate not trusted");
          server = "https://octop.internal:8443";
          return server;
        case "UseLocalMode":
          server = "";
          return;
        case "ShowMain":
          return;
        default:
          throw new Error(`Unexpected native operation: ${name}`);
      }
    },
  };
  await window.eval(`(async () => { ${script} })()`);
  return {
    dom,
    calls,
    byId: (id) => window.document.getElementById(id),
    fail: () => {
      rejectConnection = true;
    },
  };
}

test("company installer hides unavailable local runtime controls", async () => {
  const p = await panel("", false);
  try {
    assert.equal(p.byId("use-local").hidden, true);
    assert.equal(p.byId("local-note").hidden, true);
  } finally {
    p.dom.window.close();
  }
});

test("file authorization panel shows current server without another address form", async () => {
  const p = await panel("http://company.test:8088", false);
  try {
    assert.equal(p.byId("address"), null);
    assert.equal(p.byId("connect"), null);
    assert.match(p.byId("server").textContent, /company.test:8088/);
    assert.equal(p.byId("device-panel").hidden, false);
    assert.equal(p.byId("writable").checked, false);
    assert.equal(p.byId("allow-company").checked, false);
    assert.equal(p.byId("add-folder").disabled, true);
  } finally { p.dom.window.close(); }
});
