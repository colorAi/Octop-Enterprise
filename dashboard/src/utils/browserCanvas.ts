export interface CanvasPoint {
  x: number;
  y: number;
}

/** Map a screen pointer position to canvas pixel coordinates. */
export function getCanvasCoords(
  canvas: HTMLCanvasElement | null,
  e: { clientX: number; clientY: number },
): CanvasPoint {
  if (!canvas) return { x: 0, y: 0 };
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return { x: 0, y: 0 };
  let width = rect.width;
  let height = rect.height;
  let left = rect.left;
  let top = rect.top;
  if (getComputedStyle(canvas).objectFit === "contain") {
    const scale = Math.min(width / canvas.width, height / canvas.height);
    const imageWidth = canvas.width * scale;
    const imageHeight = canvas.height * scale;
    left += (width - imageWidth) / 2;
    top += (height - imageHeight) / 2;
    width = imageWidth;
    height = imageHeight;
  }
  return {
    x: Math.max(
      0,
      Math.min(
        canvas.width - 1,
        Math.round(((e.clientX - left) * canvas.width) / width),
      ),
    ),
    y: Math.max(
      0,
      Math.min(
        canvas.height - 1,
        Math.round(((e.clientY - top) * canvas.height) / height),
      ),
    ),
  };
}

/** Paint a JPEG base64 frame onto a canvas (WebSocket stream). */
export function paintBase64JpegToCanvas(
  canvas: HTMLCanvasElement | null,
  base64Data: string,
): void {
  if (!canvas) return;
  const img = new Image();
  img.onload = () => {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (canvas.width !== img.width || canvas.height !== img.height) {
      canvas.width = img.width;
      canvas.height = img.height;
    }
    ctx.imageSmoothingEnabled = true;
    if ("imageSmoothingQuality" in ctx) {
      ctx.imageSmoothingQuality = "high";
    }
    ctx.drawImage(img, 0, 0);
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
}

/** Paint a screenshot blob onto a canvas (HTTP polling). */
export async function paintBlobToCanvas(
  canvas: HTMLCanvasElement | null,
  blob: Blob,
): Promise<boolean> {
  if (!canvas) return false;
  const bitmap = await createImageBitmap(blob);
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0);
  bitmap.close();
  return true;
}

export function clearCanvas(canvas: HTMLCanvasElement | null): void {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
}
