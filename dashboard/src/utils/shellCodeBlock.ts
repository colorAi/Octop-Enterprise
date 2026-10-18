const SHELL_LANGS = new Set([
  "bash",
  "shell",
  "sh",
  "zsh",
  "fish",
  "powershell",
  "pwsh",
]);

export function isShellLanguage(lang: string): boolean {
  return SHELL_LANGS.has(lang.toLowerCase());
}

/** Extract shell code blocks from assistant markdown (in document order). */
export function extractBashBlocks(markdown: string): string[] {
  const re = /```(bash|shell|sh|zsh|fish|powershell|pwsh)\n([\s\S]*?)```/gi;
  const blocks: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = re.exec(markdown)) !== null) {
    const code = match[2].trim();
    if (code) blocks.push(code);
  }
  return blocks;
}
