package company

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

func textExtension(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".txt", ".md", ".csv", ".json", ".tsv", ".log":
		return true
	}
	return false
}
func readDocument(path string, data []byte) (string, error) {
	if textExtension(path) {
		if !utf8.Valid(data) || bytes.IndexByte(data, 0) >= 0 {
			return "", errors.New("utf8_text_required")
		}
		return string(data), nil
	}
	ext := strings.ToLower(filepath.Ext(path))
	if ext != ".docx" && ext != ".xlsx" {
		return "", errors.New("supported_formats_txt_md_csv_json_tsv_log_docx_xlsx")
	}
	files, err := archiveXML(data)
	if err != nil {
		return "", err
	}
	if ext == ".docx" {
		return documentText(files["word/document.xml"])
	}
	return spreadsheetText(files)
}

// Bounded expansion prevents compressed-document bombs. No links, macros,
// embedded objects, formulas or external relationships are ever executed.
func archiveXML(data []byte) (map[string][]byte, error) {
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil || len(z.File) > 2048 {
		return nil, errors.New("invalid_office_document")
	}
	result := map[string][]byte{}
	total := 0
	for _, f := range z.File {
		wanted := f.Name == "word/document.xml" || f.Name == "xl/sharedStrings.xml" || (strings.HasPrefix(f.Name, "xl/worksheets/sheet") && strings.HasSuffix(f.Name, ".xml"))
		if !wanted {
			continue
		}
		if _, ok := result[f.Name]; ok {
			return nil, errors.New("duplicate_document_part")
		}
		if f.UncompressedSize64 > 16*1024*1024 {
			return nil, errors.New("expanded_document_too_large")
		}
		r, err := f.Open()
		if err != nil {
			return nil, errors.New("invalid_document_part")
		}
		b, readErr := io.ReadAll(io.LimitReader(r, 16*1024*1024+1))
		_ = r.Close()
		total += len(b)
		if readErr != nil || len(b) > 16*1024*1024 || total > 32*1024*1024 {
			return nil, errors.New("expanded_document_too_large")
		}
		result[f.Name] = b
	}
	return result, nil
}
func documentText(b []byte) (string, error) {
	if len(b) == 0 {
		return "", errors.New("document_body_missing")
	}
	dec := xml.NewDecoder(bytes.NewReader(b))
	var out strings.Builder
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", errors.New("invalid_document_xml")
		}
		switch t := tok.(type) {
		case xml.StartElement:
			if t.Name.Local == "t" {
				var s string
				if dec.DecodeElement(&s, &t) != nil {
					return "", errors.New("invalid_document_xml")
				}
				out.WriteString(s)
			}
			if t.Name.Local == "tab" {
				out.WriteByte('\t')
			}
			if t.Name.Local == "br" {
				out.WriteByte('\n')
			}
		case xml.EndElement:
			if t.Name.Local == "p" {
				out.WriteByte('\n')
			}
		}
		if out.Len() > maxContent {
			break
		}
	}
	return out.String(), nil
}

type richText struct {
	Text string `xml:"t"`
	Runs []struct {
		Text string `xml:"t"`
	} `xml:"r"`
}

func (r richText) value() string {
	value := r.Text
	for _, run := range r.Runs {
		value += run.Text
	}
	return value
}
func spreadsheetText(files map[string][]byte) (string, error) {
	var shared struct {
		Items []richText `xml:"si"`
	}
	if b := files["xl/sharedStrings.xml"]; b != nil {
		if xml.Unmarshal(b, &shared) != nil {
			return "", errors.New("invalid_shared_strings")
		}
	}
	names := []string{}
	for name := range files {
		if strings.HasPrefix(name, "xl/worksheets/") {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		return "", errors.New("spreadsheet_missing")
	}
	var out strings.Builder
	out.WriteString("XLSX stored cell values (formulas are cached, not recalculated; formatting is omitted).\n")
	for _, name := range names {
		var sheet struct {
			Rows []struct {
				Cells []struct {
					Ref     string   `xml:"r,attr"`
					Type    string   `xml:"t,attr"`
					Value   string   `xml:"v"`
					Inline  richText `xml:"is"`
					Formula string   `xml:"f"`
				} `xml:"c"`
			} `xml:"sheetData>row"`
		}
		if xml.Unmarshal(files[name], &sheet) != nil {
			return "", errors.New("invalid_sheet_xml")
		}
		fmt.Fprintf(&out, "\n[%s]\n", name)
		for _, row := range sheet.Rows {
			for _, cell := range row.Cells {
				value := cell.Value
				if cell.Type == "s" {
					index, err := strconv.Atoi(value)
					if err != nil || index < 0 || index >= len(shared.Items) {
						return "", errors.New("invalid_shared_string_index")
					}
					value = shared.Items[index].value()
				}
				if cell.Type == "inlineStr" {
					value = cell.Inline.value()
				}
				if cell.Formula != "" {
					value += " [cached formula]"
				}
				fmt.Fprintf(&out, "%s\t%s\n", cell.Ref, value)
				if out.Len() > maxContent {
					return out.String(), nil
				}
			}
		}
	}
	return out.String(), nil
}
func escaped(s string) string {
	var b bytes.Buffer
	_ = xml.EscapeText(&b, []byte(s))
	return b.String()
}
func writeDocument(path, content string) ([]byte, error) {
	if !utf8.ValidString(content) || strings.ContainsRune(content, 0) {
		return nil, errors.New("utf8_text_required")
	}
	ext := strings.ToLower(filepath.Ext(path))
	if textExtension(path) {
		if ext == ".csv" {
			rows, err := csv.NewReader(strings.NewReader(content)).ReadAll()
			if err != nil {
				return nil, errors.New("invalid_csv")
			}
			var b bytes.Buffer
			w := csv.NewWriter(&b)
			for _, row := range rows {
				for i, cell := range row {
					check := strings.TrimLeft(cell, " \t\r\n")
					if strings.HasPrefix(check, "=") || strings.HasPrefix(check, "+") || strings.HasPrefix(check, "-") || strings.HasPrefix(check, "@") {
						row[i] = "'" + cell
					}
				}
				_ = w.Write(row)
			}
			w.Flush()
			return b.Bytes(), w.Error()
		}
		return []byte(content), nil
	}
	parts := map[string]string{}
	switch ext {
	case ".docx":
		body := ""
		for _, p := range strings.Split(content, "\n") {
			body += "<w:p><w:r><w:t xml:space=\"preserve\">" + escaped(p) + "</w:t></w:r></w:p>"
		}
		parts["word/document.xml"] = `<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>` + body + `</w:body></w:document>`
		parts["_rels/.rels"] = relationships("officeDocument", "word/document.xml")
		parts["[Content_Types].xml"] = contentTypes(`<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>`)
	case ".xlsx":
		rows, err := csv.NewReader(strings.NewReader(content)).ReadAll()
		if err != nil || len(rows) > 10000 {
			return nil, errors.New("xlsx_content_must_be_csv")
		}
		var sheet strings.Builder
		sheet.WriteString(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>`)
		for r, row := range rows {
			if len(row) > 256 {
				return nil, errors.New("too_many_columns")
			}
			fmt.Fprintf(&sheet, `<row r="%d">`, r+1)
			for col, cell := range row {
				fmt.Fprintf(&sheet, `<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>`, columnName(col+1), r+1, escaped(cell))
			}
			sheet.WriteString(`</row>`)
		}
		sheet.WriteString(`</sheetData></worksheet>`)
		parts["xl/worksheets/sheet1.xml"] = sheet.String()
		parts["xl/workbook.xml"] = `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>`
		parts["xl/_rels/workbook.xml.rels"] = relationships("worksheet", "worksheets/sheet1.xml")
		parts["_rels/.rels"] = relationships("officeDocument", "xl/workbook.xml")
		parts["[Content_Types].xml"] = contentTypes(`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`)
	default:
		return nil, errors.New("output_format_not_supported")
	}
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for name, content := range parts {
		w, err := z.Create(name)
		if err != nil {
			return nil, err
		}
		if _, err = w.Write([]byte(content)); err != nil {
			return nil, err
		}
	}
	if err := z.Close(); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}
func relationships(kind, target string) string {
	return `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/` + kind + `" Target="` + target + `"/></Relationships>`
}
func contentTypes(extra string) string {
	return `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>` + extra + `</Types>`
}

func columnName(number int) string {
	name := ""
	for number > 0 {
		number--
		name = string(rune('A'+number%26)) + name
		number /= 26
	}
	return name
}
