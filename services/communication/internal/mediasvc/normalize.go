// Upload normalisation (F11): EXIF orientation is applied and metadata is
// stripped before originals are persisted, so stored pixels match displayed
// pixels and no GPS/device metadata is retained.
package mediasvc

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
)

// exifOrientation returns the EXIF Orientation tag (0x0112, values 1..8) or
// 0 when absent/unparseable. JPEG: APP1 "Exif" segment; PNG: eXIf chunk.
func exifOrientation(data []byte) int {
	var tiff []byte
	switch {
	case bytes.HasPrefix(data, []byte{0xFF, 0xD8}): // JPEG
		for i := 2; i+4 <= len(data); {
			if data[i] != 0xFF {
				break
			}
			marker := data[i+1]
			if marker == 0x01 || (marker >= 0xD0 && marker <= 0xD9) { // standalone (TEM/RST/SOI)
				i += 2
				continue
			}
			if i+4 > len(data) {
				break
			}
			segLen := int(binary.BigEndian.Uint16(data[i+2 : i+4]))
			if segLen < 2 {
				break
			}
			if marker == 0xE1 && segLen >= 8 && bytes.HasPrefix(data[i+4:], []byte("Exif\x00\x00")) {
				end := i + 2 + segLen
				if end > len(data) {
					end = len(data)
				}
				tiff = data[i+10 : end]
				break
			}
			if marker == 0xDA { // start of scan: stop scanning
				break
			}
			i += 2 + segLen
		}
	case bytes.HasPrefix(data, []byte("\x89PNG\r\n\x1a\n")):
		for i := 8; i+8 <= len(data); {
			chLen := int(binary.BigEndian.Uint32(data[i : i+4]))
			typ := string(data[i+4 : i+8])
			if typ == "eXIf" && i+8+chLen <= len(data) {
				tiff = data[i+8 : i+8+chLen]
				break
			}
			if typ == "IDAT" || typ == "IEND" {
				break
			}
			i += 12 + chLen
		}
	}
	if len(tiff) < 8 {
		return 0
	}
	var bo binary.ByteOrder
	switch string(tiff[:2]) {
	case "II":
		bo = binary.LittleEndian
	case "MM":
		bo = binary.BigEndian
	default:
		return 0
	}
	ifdOff := int(bo.Uint32(tiff[4:8]))
	if ifdOff < 2 || ifdOff+2 > len(tiff) {
		return 0
	}
	n := int(bo.Uint16(tiff[ifdOff : ifdOff+2]))
	for e := 0; e < n; e++ {
		off := ifdOff + 2 + e*12
		if off+12 > len(tiff) {
			return 0
		}
		if bo.Uint16(tiff[off:off+2]) == 0x0112 {
			v := int(bo.Uint16(tiff[off+8 : off+10])) // SHORT value, inline
			if v >= 1 && v <= 8 {
				return v
			}
			return 0
		}
	}
	return 0
}

// normalizeImage decodes, applies EXIF orientation, and re-encodes
// (which drops all remaining metadata). Returns an error when the image
// cannot be decoded; callers keep the original bytes in that case.
func normalizeImage(data []byte, mime string) ([]byte, error) {
	img, err := decodeFull(data, mime)
	if err != nil {
		return nil, err
	}
	o := exifOrientation(data)
	if o <= 1 {
		// still re-encode to strip metadata
	} else {
		img = applyOrientation(img, o)
	}
	var buf bytes.Buffer
	switch mime {
	case "image/jpeg":
		if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: 85}); err != nil {
			return nil, err
		}
	case "image/png":
		if err := png.Encode(&buf, img); err != nil {
			return nil, err
		}
	default:
		return nil, fmt.Errorf("not normalizable: %s", mime)
	}
	return buf.Bytes(), nil
}

func applyOrientation(src image.Image, o int) image.Image {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	dstW, dstH := w, h
	if o >= 5 {
		dstW, dstH = h, w
	}
	dst := image.NewRGBA(image.Rect(0, 0, dstW, dstH))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			c := src.At(b.Min.X+x, b.Min.Y+y)
			var dx, dy int
			switch o {
			case 2: // flip horizontal
				dx, dy = w-1-x, y
			case 3: // rotate 180
				dx, dy = w-1-x, h-1-y
			case 4: // flip vertical
				dx, dy = x, h-1-y
			case 5: // transpose (mirror along top-left diagonal)
				dx, dy = y, x
			case 6: // rotate 90° CW
				dx, dy = h-1-y, x
			case 7: // transverse (mirror along bottom-right diagonal)
				dx, dy = h-1-y, w-1-x
			case 8: // rotate 270° CW
				dx, dy = y, w-1-x
			default:
				dx, dy = x, y
			}
			r, g, bl, a := c.RGBA()
			dst.SetRGBA(dx, dy, color.RGBA{uint8(r >> 8), uint8(g >> 8), uint8(bl >> 8), uint8(a >> 8)})
		}
	}
	return dst
}
