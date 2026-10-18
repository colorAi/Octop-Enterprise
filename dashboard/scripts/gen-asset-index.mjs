import { readdirSync, readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";

const assetsDir = new URL("../public/assets", import.meta.url).pathname;

function naturalSort(arr) {
  return arr.sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }),
  );
}

// asset-index.json
const floors = naturalSort(
  readdirSync(join(assetsDir, "floors")).filter((f) => f.endsWith(".png")),
).map((f) => `floors/${f}`);
const walls = naturalSort(
  readdirSync(join(assetsDir, "walls")).filter((f) => f.endsWith(".png")),
).map((f) => `walls/${f}`);
const characters = naturalSort(
  readdirSync(join(assetsDir, "characters")).filter((f) => f.endsWith(".png")),
).map((f) => `characters/${f}`);

const assetIndex = {
  floors,
  walls,
  characters,
  defaultLayout: "default-layout-1.json",
};
writeFileSync(
  join(assetsDir, "asset-index.json"),
  JSON.stringify(assetIndex, null, 2),
);
console.log(
  `asset-index.json: ${floors.length} floors, ${walls.length} walls, ${characters.length} chars`,
);

// furniture-catalog.json
const catalog = [];
const furnitureDir = join(assetsDir, "furniture");
const folders = naturalSort(readdirSync(furnitureDir));

for (const folder of folders) {
  const manifestPath = join(furnitureDir, folder, "manifest.json");
  if (!existsSync(manifestPath)) continue;
  const m = JSON.parse(readFileSync(manifestPath, "utf8"));

  if (m.type === "group") {
    for (const member of m.members || []) {
      const entry = {
        id: member.id,
        name: m.name || member.id,
        label: m.name || member.id,
        category: m.category || "misc",
        file: member.file || `${member.id}.png`,
        furniturePath: `furniture/${folder}/${
          member.file || member.id + ".png"
        }`,
        width: member.width || 16,
        height: member.height || 16,
        footprintW: member.footprintW || 1,
        footprintH: member.footprintH || 1,
        isDesk: m.isDesk || false,
        canPlaceOnWalls: m.canPlaceOnWalls || false,
        groupId: folder,
        rotationScheme: m.rotationScheme || undefined,
      };
      if (member.orientation) entry.orientation = member.orientation;
      if (member.mirrorSide) entry.mirrorSide = member.mirrorSide;
      if (m.canPlaceOnSurfaces != null)
        entry.canPlaceOnSurfaces = m.canPlaceOnSurfaces;
      if (m.backgroundTiles != null) entry.backgroundTiles = m.backgroundTiles;
      catalog.push(entry);
    }
  } else {
    const pngs = readdirSync(join(furnitureDir, folder)).filter((f) =>
      f.endsWith(".png"),
    );
    const pngFile = pngs[0] || `${folder}.png`;
    const entry = {
      id: m.id || folder,
      name: m.name || folder,
      label: m.name || folder,
      category: m.category || "misc",
      file: pngFile,
      furniturePath: `furniture/${folder}/${pngFile}`,
      width: m.width || 16,
      height: m.height || 16,
      footprintW: m.footprintW || 1,
      footprintH: m.footprintH || 1,
      isDesk: m.isDesk || false,
      canPlaceOnWalls: m.canPlaceOnWalls || false,
    };
    if (m.canPlaceOnSurfaces != null)
      entry.canPlaceOnSurfaces = m.canPlaceOnSurfaces;
    if (m.backgroundTiles != null) entry.backgroundTiles = m.backgroundTiles;
    if (m.animationGroup) entry.animationGroup = m.animationGroup;
    if (m.frame != null) entry.frame = m.frame;
    if (m.state) entry.state = m.state;
    catalog.push(entry);
  }
}

writeFileSync(
  join(assetsDir, "furniture-catalog.json"),
  JSON.stringify(catalog, null, 2),
);
console.log(`furniture-catalog.json: ${catalog.length} entries`);
