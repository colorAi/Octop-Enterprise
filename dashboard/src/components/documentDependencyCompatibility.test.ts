import { describe, expect, it } from "vitest";
import * as xlsx from "xlsx";
import DOMPurify from "dompurify";
import katex from "katex";
import mermaid from "mermaid";

describe("document dependency compatibility", () => {
  it.each(["xlsx", "xls", "csv"] as const)(
    "preserves table data when parsing %s",
    (bookType) => {
      const book = xlsx.utils.book_new();
      xlsx.utils.book_append_sheet(
        book,
        xlsx.utils.aoa_to_sheet([
          ["名称", "数量"],
          ["Octop", 42],
        ]),
        "Data",
      );
      const bytes = xlsx.write(book, { type: "array", bookType });
      const parsed = xlsx.read(bytes, { type: "array", cellDates: true });
      expect(
        xlsx.utils.sheet_to_json(parsed.Sheets[parsed.SheetNames[0]], {
          header: 1,
        }),
      ).toEqual([
        ["名称", "数量"],
        ["Octop", 42],
      ]);
    },
  );
  it("sanitizes untrusted HTML and renders math using the patched packages", () => {
    expect(
      DOMPurify.sanitize(
        '<img src="x" onerror="alert(1)"><script>alert(1)</script>',
      ),
    ).not.toMatch(/onerror|script/);
    const html = katex.renderToString("x^2 + \\frac{1}{2}", {
      throwOnError: false,
      trust: false,
    });
    expect(html).toContain("katex-mathml");
    expect(
      katex.renderToString("\\href{javascript:alert(1)}{x}", {
        throwOnError: false,
        trust: false,
      }),
    ).not.toContain('href="javascript:');
  });
  it("parses existing Mermaid flowcharts after the security updates", async () => {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
    expect(
      await mermaid.parse(
        "flowchart LR\n A[员工] --> B[本地运行时] --> C[公司服务]",
      ),
    ).toBeTruthy();
  });
});
