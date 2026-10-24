import { describe, expect, it } from "vitest";
import { getCanvasCoords } from "./browserCanvas";

function canvasAt(width: number, height: number, fit = "contain") {
  const canvas = document.createElement("canvas");
  canvas.width = 1280;
  canvas.height = 800;
  canvas.style.objectFit = fit;
  canvas.getBoundingClientRect = () =>
    ({ left: 20, top: 60, width, height }) as DOMRect;
  return canvas;
}

describe("browser canvas pointer coordinates", () => {
  it("subtracts horizontal letterboxing before scaling clicks", () => {
    // Image is 960x600 inside a 1200x600 canvas: 120px bars at each side.
    const canvas = canvasAt(1200, 600);
    expect(getCanvasCoords(canvas, { clientX: 215, clientY: 210 })).toEqual({
      x: 100,
      y: 200,
    });
  });

  it("subtracts vertical letterboxing in tall or narrow panels", () => {
    // Image is 640x400 inside a 640x600 canvas: 100px bars top and bottom.
    const canvas = canvasAt(640, 600);
    expect(getCanvasCoords(canvas, { clientX: 70, clientY: 260 })).toEqual({
      x: 100,
      y: 200,
    });
  });

  it("keeps drag endpoints within the remote viewport", () => {
    const canvas = canvasAt(640, 600);
    expect(getCanvasCoords(canvas, { clientX: -10, clientY: 0 })).toEqual({
      x: 0,
      y: 0,
    });
    expect(getCanvasCoords(canvas, { clientX: 1000, clientY: 1000 })).toEqual({
      x: 1279,
      y: 799,
    });
  });

  it("preserves coordinate scaling for stretched canvases", () => {
    const canvas = canvasAt(640, 600, "fill");
    expect(getCanvasCoords(canvas, { clientX: 70, clientY: 210 })).toEqual({
      x: 100,
      y: 200,
    });
  });

  it("returns the origin before the canvas has layout", () => {
    expect(getCanvasCoords(null, { clientX: 10, clientY: 20 })).toEqual({
      x: 0,
      y: 0,
    });
    expect(
      getCanvasCoords(canvasAt(0, 0), { clientX: 10, clientY: 20 }),
    ).toEqual({
      x: 0,
      y: 0,
    });
  });
});
