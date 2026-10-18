/** Lazy mermaid core — diagram chunks load on first render of each type. */
let mermaidModule: typeof import("mermaid")["default"] | null = null;
let mermaidInitialised = false;

export async function loadMermaid() {
  if (!mermaidModule) {
    const mod = await import("mermaid");
    mermaidModule = mod.default;
  }
  if (!mermaidInitialised) {
    mermaidInitialised = true;
    mermaidModule.initialize({
      startOnLoad: false,
      theme: "default",
      securityLevel: "strict",
      fontFamily: "inherit",
    });
  }
  return mermaidModule;
}
