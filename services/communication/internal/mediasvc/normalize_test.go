package mediasvc

import (
	"bytes"
	"encoding/binary"
	"image"
	"image/color"
	"image/jpeg"
	"testing"
)

// jpegWithOrientation encodes img as JPEG and injects an APP1 Exif segment
// carrying only the Orientation tag with the given value.
func jpegWithOrientation(t *testing.T, img image.Image, orientation int) []byte {
	t.Helper()
	var raw bytes.Buffer
	if err := jpeg.Encode(&raw, img, &jpeg.Options{Quality: 90}); err != nil {
		t.Fatal(err)
	}
	// little-endian TIFF block: header + IFD offset + 1 entry + next-ifd
	var tiff bytes.Buffer
	tiff.Write([]byte("II"))
	binary.Write(&tiff, binary.LittleEndian, uint16(42))
	binary.Write(&tiff, binary.LittleEndian, uint32(8))
	binary.Write(&tiff, binary.LittleEndian, uint16(1)) // one entry
	binary.Write(&tiff, binary.LittleEndian, uint16(0x0112))
	binary.Write(&tiff, binary.LittleEndian, uint16(3))           // SHORT
	binary.Write(&tiff, binary.LittleEndian, uint32(1))           // count
	binary.Write(&tiff, binary.LittleEndian, uint32(orientation)) // value+padding
	binary.Write(&tiff, binary.LittleEndian, uint32(0))           // next IFD
	app1 := append([]byte("Exif\x00\x00"), tiff.Bytes()...)
	out := append([]byte{}, raw.Bytes()[:2]...)
	out = append(out, 0xFF, 0xE1, byte((len(app1)+2)>>8), byte((len(app1)+2)&0xFF))
	out = append(out, app1...)
	return append(out, raw.Bytes()[2:]...)
}

func TestExifOrientationParsed(t *testing.T) {
	src := image.NewGray(image.Rect(0, 0, 2, 1)) // luminance only: survives jpeg re-encode
	src.SetGray(0, 0, color.Gray{Y: 255})
	src.SetGray(1, 0, color.Gray{Y: 0})
	data := jpegWithOrientation(t, src, 6) // rotate 90° CW
	if got := exifOrientation(data); got != 6 {
		t.Fatalf("orientation=%d, want 6", got)
	}
	norm, err := normalizeImage(data, "image/jpeg")
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := jpeg.DecodeConfig(bytes.NewReader(norm))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Width != 1 || cfg.Height != 2 {
		t.Fatalf("orientation not applied: %dx%d, want 1x2", cfg.Width, cfg.Height)
	}
	// 90° CW maps (x,y)->(h-1-y,x): bright (0,0) → (0,0); dark (1,0) → (0,1)
	img, err := jpeg.Decode(bytes.NewReader(norm))
	if err != nil {
		t.Fatal(err)
	}
	l0, _, _, _ := img.At(0, 0).RGBA()
	l1, _, _, _ := img.At(0, 1).RGBA()
	if l0 <= l1 {
		t.Fatalf("rotation mapping wrong: (0,0)=%d (0,1)=%d", l0, l1)
	}
	// metadata stripped: re-encoded output has no EXIF orientation
	if exifOrientation(norm) != 0 {
		t.Fatal("metadata not stripped")
	}
}

func TestExifOrientationAbsent(t *testing.T) {
	var buf bytes.Buffer
	jpeg.Encode(&buf, image.NewGray(image.Rect(0, 0, 3, 3)), nil)
	if got := exifOrientation(buf.Bytes()); got != 0 {
		t.Fatalf("orientation=%d, want 0", got)
	}
}
