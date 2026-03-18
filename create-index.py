#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import importlib
import re
from collections import deque
from pathlib import Path
from typing import Any, Dict
import unicodedata


IMAGE_EXTENSIONS = {".png"}

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s.-]", " ", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")

def get_pil_modules():
    image_module = importlib.import_module("PIL.Image")
    draw_module = importlib.import_module("PIL.ImageDraw")
    return image_module, draw_module


def load_image(path: str) -> Any:
    image_module, _ = get_pil_modules()
    return image_module.open(path).convert("RGBA")


def is_transparent(alpha: int, threshold: int) -> bool:
    return alpha <= threshold


def find_transparent_components(img: Any, alpha_threshold: int):
    width, height = img.size
    px = img.load()

    visited = [[False] * width for _ in range(height)]
    components = []
    directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    for y in range(height):
        for x in range(width):
            if visited[y][x]:
                continue

            visited[y][x] = True
            if not is_transparent(px[x, y][3], alpha_threshold):
                continue

            q = deque([(x, y)])
            pixels = []
            min_x = max_x = x
            min_y = max_y = y
            area = 0
            touches_border = False

            while q:
                cx, cy = q.popleft()
                pixels.append((cx, cy))
                area += 1

                if cx < min_x:
                    min_x = cx
                if cy < min_y:
                    min_y = cy
                if cx > max_x:
                    max_x = cx
                if cy > max_y:
                    max_y = cy

                if cx == 0 or cy == 0 or cx == width - 1 or cy == height - 1:
                    touches_border = True

                for dx, dy in directions:
                    nx, ny = cx + dx, cy + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if visited[ny][nx]:
                        continue

                    visited[ny][nx] = True
                    if is_transparent(px[nx, ny][3], alpha_threshold):
                        q.append((nx, ny))

            comp_width = max_x - min_x + 1
            comp_height = max_y - min_y + 1
            bbox_area = comp_width * comp_height
            fill_ratio = area / bbox_area if bbox_area else 0.0

            components.append({
                "min_x": min_x,
                "min_y": min_y,
                "max_x": max_x,
                "max_y": max_y,
                "width": comp_width,
                "height": comp_height,
                "area": area,
                "bbox_area": bbox_area,
                "fill_ratio": fill_ratio,
                "touches_border": touches_border,
                "pixels": pixels,
            })

    return components


def choose_best_component(components):
    inner = [c for c in components if not c["touches_border"]]
    if not inner:
        raise RuntimeError(
            "Kein innerer transparenter Bereich gefunden. "
            "Die Transparenz berührt evtl. den Bildrand oder das PNG hat keinen transparenten Screen-Slot."
        )

    def score(c):
        # Bevorzuge große, kompakte, rechteckähnliche Flächen
        return c["area"] * 0.8 + c["bbox_area"] * 0.2 + c["fill_ratio"] * 1000

    inner.sort(key=score, reverse=True)
    return inner[0]


def estimate_radius(img: Any, slot: dict, alpha_threshold: int) -> int:
    px = img.load()

    x = slot["min_x"]
    y = slot["min_y"]
    max_x = slot["max_x"]
    max_y = slot["max_y"]

    def measure(threshold: int) -> int:
        samples = []

        def transparent(xx, yy):
            return is_transparent(px[xx, yy][3], threshold)

        # top-left
        for xx in range(x, max_x + 1):
            if transparent(xx, y):
                samples.append(xx - x)
                break
        for yy in range(y, max_y + 1):
            if transparent(x, yy):
                samples.append(yy - y)
                break

        # top-right
        for xx in range(max_x, x - 1, -1):
            if transparent(xx, y):
                samples.append(max_x - xx)
                break
        for yy in range(y, max_y + 1):
            if transparent(max_x, yy):
                samples.append(yy - y)
                break

        # bottom-left
        for xx in range(x, max_x + 1):
            if transparent(xx, max_y):
                samples.append(xx - x)
                break
        for yy in range(max_y, y - 1, -1):
            if transparent(x, yy):
                samples.append(max_y - yy)
                break

        # bottom-right
        for xx in range(max_x, x - 1, -1):
            if transparent(xx, max_y):
                samples.append(max_x - xx)
                break
        for yy in range(max_y, y - 1, -1):
            if transparent(max_x, yy):
                samples.append(max_y - yy)
                break

        samples = [s for s in samples if s >= 0]
        if not samples:
            return 0

        samples.sort()
        return int(round(samples[len(samples) // 2]))

    # Try multiple thresholds to handle large, anti-aliased rounded corners.
    thresholds = [0]
    if alpha_threshold > 0:
        thresholds.append(alpha_threshold)
    thresholds.extend([40, 80, 120, 160, 800])

    radius = 0
    for threshold in thresholds:
        radius = max(radius, measure(threshold))

    return radius


def draw_debug_overlay(
    img: Any,
    slot: dict,
    out_path: str,
    alpha_threshold: int,
    component_pixels=None,
):
    image_module, draw_module = get_pil_modules()
    debug = img.copy()
    draw = draw_module.Draw(debug, "RGBA")

    x = slot["x"]
    y = slot["y"]
    w = slot["width"]
    h = slot["height"]
    r = slot["radius"]

    # Optionale Visualisierung der erkannten Pixel-Fläche
    if component_pixels:
        overlay = image_module.new("RGBA", debug.size, (0, 0, 0, 0))
        overlay_px = overlay.load()
        for px, py in component_pixels:
            overlay_px[px, py] = (255, 0, 0, 60)
        debug = image_module.alpha_composite(debug, overlay)
        draw = draw_module.Draw(debug, "RGBA")

    # Bounding box
    draw.rectangle(
        [x, y, x + w - 1, y + h - 1],
        outline=(255, 0, 0, 255),
        width=3,
    )

    # Rounded rectangle
    if r > 0:
        draw.rounded_rectangle(
            [x, y, x + w - 1, y + h - 1],
            radius=r,
            outline=(0, 255, 0, 255),
            width=3,
        )

    # Crosshair in der Mitte
    cx = x + w // 2
    cy = y + h // 2
    draw.line([cx - 20, cy, cx + 20, cy], fill=(0, 128, 255, 255), width=2)
    draw.line([cx, cy - 20, cx, cy + 20], fill=(0, 128, 255, 255), width=2)

    # Text-Hintergrund
    label = f"x={x}, y={y}, w={w}, h={h}, r={r}, alpha<={alpha_threshold}"
    tx = max(10, x)
    ty = max(10, y - 28)
    draw.rectangle([tx - 4, ty - 2, tx + len(label) * 7, ty + 18], fill=(0, 0, 0, 180))
    draw.text((tx, ty), label, fill=(255, 255, 255, 255))

    debug.save(out_path)


def analyze_image(path: str, alpha_threshold: int):
    img = load_image(path)
    width, height = img.size

    components = find_transparent_components(img, alpha_threshold)
    best = choose_best_component(components)
    radius = estimate_radius(img, best, alpha_threshold)

    result = {
        "file": str(Path(path).resolve()),
        "imageWidth": width,
        "imageHeight": height,
        "alphaThreshold": alpha_threshold,
        "slot": {
            "x": best["min_x"],
            "y": best["min_y"],
            "width": best["width"],
            "height": best["height"],
            "radius": radius,
        },
        "detection": {
            "transparentComponents": len(components),
            "chosenArea": best["area"],
            "chosenFillRatio": round(best["fill_ratio"], 4),
            "touchesBorder": best["touches_border"],
        },
        "_component_pixels": best["pixels"],  # intern für Debug-Bild
    }

    return result




class SimpleProgress:
    def __init__(self, total: int):
        self.total = total
        self.count = 0

    def update(self, n: int = 1):
        self.count += n
        print(f"{self.count}/{self.total}", end="\r")

    def close(self):
        print("")


def get_progress(total: int):
    try:
        from tqdm import tqdm  # type: ignore[import-not-found]

        return tqdm(total=total)
    except ImportError:
        return SimpleProgress(total)


def detect_orientation(filename: str, width: int, height: int, device_type: str) -> str:
    if device_type in {"phone", "tablet"}:
        return "landscape" if width > height else "portrait"

    lower = filename.lower()
    if "portrait" in lower:
        return "portrait"
    return "landscape"


def infer_color_from_filename(stem: str) -> str:
    parts = [p.strip() for p in re.split(r"\s*-\s*", stem) if p.strip()]
    color_raw = parts[-1] if parts else stem
    return normalize_device_name(color_raw).replace(" ", "-")


def normalize_device_name(name: str) -> str:
    name = name.lower().strip()
    name = name.replace("&", "and")
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def build_device_keys(name: str) -> set[str]:
    keys = set()
    normalized = normalize_device_name(name)
    if normalized:
        keys.add(normalized)

    for prefix in (
        "apple ",
        "samsung ",
        "google ",
        "microsoft ",
        "lg ",
        "htc ",
        "oneplus ",
        "motorola ",
        "asus ",
        "amazon ",
        "blackberry ",
    ):
        if normalized.startswith(prefix):
            keys.add(normalized[len(prefix) :].strip())

    return keys


def load_device_info(device_info_dir: Path) -> dict[str, dict[str, dict[str, str]]]:
    datasets: dict[str, dict[str, dict[str, str]]] = {"phone": {}, "tablet": {}}

    sources = {
        "phone": device_info_dir / "smartphones.csv",
        "tablet": device_info_dir / "tablets.csv",
    }

    for device_type, csv_path in sources.items():
        if not csv_path.exists():
            continue

        with csv_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                name = row.get("name") or ""
                if not name:
                    continue
                for key in build_device_keys(name):
                    datasets[device_type][key] = row

    return datasets


def find_device_info(
    device_name: str,
    device_type: str,
    datasets: dict[str, dict[str, dict[str, str]]],
) -> dict[str, str] | None:
    if device_type not in {"phone", "tablet"}:
        return None

    normalized = normalize_device_name(device_name)
    if not normalized:
        return None

    dataset = datasets.get(device_type, {})

    if normalized in dataset:
        return dataset[normalized]

    best_key = ""
    best_row: dict[str, str] | None = None
    for key, row in dataset.items():
        if key in normalized or normalized in key:
            if len(key) > len(best_key):
                best_key = key
                best_row = row

    return best_row


def infer_device_and_manufacturer(path: Path) -> tuple[str, str]:
    parent = path.parent
    device = parent.name
    manufacturer = parent.parent.name if parent.parent else ""
    return device, manufacturer


def infer_device_type(path: Path, src_root: Path) -> str:
    parts = [p.lower() for p in path.relative_to(src_root).parts]
    for key in ("desktop", "laptop", "phone", "tablet"):
        if key in parts:
            return key
    return "unknown"


def infer_os(path: Path, src_root: Path) -> str:
    parts = [p.lower() for p in path.relative_to(src_root).parts]
    for key in ("macos", "windows", "ios", "android", "ipados"):
        if key in parts:
            return key
    return "unknown"


def load_existing(output_path: Path) -> Dict[str, Any]:
    if not output_path.exists():
        return {}
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create devices.json by detecting screen slots for all images in a folder."
    )
    parser.add_argument("source", type=Path, help="Folder with device frame images")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("devices.json"),
        help="Output JSON file (default: devices.json)",
    )
    parser.add_argument(
        "--alpha",
        type=int,
        default=10,
        help="Alpha threshold used by detector (default: 10)",
    )
    args = parser.parse_args()

    src_root = args.source.resolve()
    output_path = args.output.resolve()
    device_info_dir = Path(__file__).resolve().parent / "device-info"
    device_info = load_device_info(device_info_dir)

    if not src_root.exists() or not src_root.is_dir():
        print(f"Invalid source directory: {src_root}")
        return 1

    existing = load_existing(output_path)

    files = [
        p
        for p in src_root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    progress = get_progress(len(files))

    def flush_output():
        output_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    debug_root = src_root.parent / "debug"

    for file_path in files:
        rel_key = file_path.relative_to(src_root).as_posix()
        if rel_key in existing:
            progress.update(1)
            continue

        try:
            result = analyze_image(str(file_path), args.alpha)
            slot = result["slot"]

            device, manufacturer = infer_device_and_manufacturer(file_path)
            device_type = infer_device_type(file_path, src_root)
            os_name = infer_os(file_path, src_root)
            orientation = detect_orientation(
                file_path.stem,
                result["imageWidth"],
                result["imageHeight"],
                device_type,
            )
            color = infer_color_from_filename(file_path.stem)

            debug_path = (debug_root / file_path.relative_to(src_root)).with_suffix(".png")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            img = load_image(str(file_path))
            draw_debug_overlay(
                img=img,
                slot={
                    "x": slot["x"],
                    "y": slot["y"],
                    "width": slot["width"],
                    "height": slot["height"],
                    "radius": slot["radius"],
                },
                out_path=str(debug_path),
                alpha_threshold=result["alphaThreshold"],
                component_pixels=result.get("_component_pixels"),
            )

            entry = {
                "id": f"{slugify(manufacturer)}-{slugify(device)}-{slugify(color)}",
                "device": device,
                "manufacturer": manufacturer,
                "type": device_type,
                "os": os_name,
                "orientation": orientation,
                "color": color,
                "image_width": result["imageWidth"],
                "image_height": result["imageHeight"],
                "slot_x": slot["x"],
                "slot_y": slot["y"],
                "slot_width": slot["width"],
                "slot_height": slot["height"],
                "slot_radius": slot["radius"],
            }

            info = find_device_info(device, device_type, device_info)
            if info:
                entry.update(
                    {
                        "spec_name": info.get("name"),
                        "spec_phys_width": info.get("phys. width"),
                        "spec_phys_height": info.get("phys. height"),
                        "spec_css_width": info.get("CSS width"),
                        "spec_css_height": info.get("CSS height"),
                        "spec_pixel_ratio": info.get("pixel ratio"),
                        "spec_phys_ppi": info.get("phys. ppi"),
                        "spec_css_ppi": info.get("CSS ppi"),
                    }
                )

            existing[rel_key] = entry
        except (OSError, ValueError, RuntimeError) as exc:
            existing[rel_key] = {
                "error": str(exc),
            }

        flush_output()

        progress.update(1)

    progress.close()

    print(f"Saved {len(existing)} entries to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
