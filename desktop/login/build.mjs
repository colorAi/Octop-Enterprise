import { readFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const require = createRequire(
  new URL("../../dashboard/package.json", import.meta.url),
);
const { build } = require("esbuild");
await build({
  entryPoints: [fileURLToPath(new URL("./main.tsx", import.meta.url))],
  bundle: true,
  format: "esm",
  minify: true,
  jsx: "automatic",
  nodePaths: [
    fileURLToPath(new URL("../../dashboard/node_modules", import.meta.url)),
  ],
  external: ["/wails/runtime.js"],
  outfile: fileURLToPath(
    new URL("../src/assets/login.bundle.js", import.meta.url),
  ),
  define: { "process.env.NODE_ENV": '"production"' },
  target: ["safari16", "chrome110"],
});

const assets = new URL("../src/assets/", import.meta.url);
const html = await readFile(new URL("login.html", assets), "utf8");
for (const [, name] of html.matchAll(
  /(?:src|href)="\/(login\.bundle\.(?:js|css))"/g,
)) {
  if ((await stat(new URL(name, assets))).size === 0)
    throw new Error(`Empty login asset: ${name}`);
}
