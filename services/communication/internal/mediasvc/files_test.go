package mediasvc

import "testing"

func TestCleanFilename(t *testing.T) {
	for input, want := range map[string]string{"../报告.pdf": "报告.pdf", "C:\\folder\\report.txt": "report.txt", "report\r\n.txt": "report.txt", "": "download"} {
		if got := cleanFilename(input); got != want {
			t.Fatalf("cleanFilename(%q)=%q want %q", input, got, want)
		}
	}
}
