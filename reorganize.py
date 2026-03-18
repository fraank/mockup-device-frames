#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import re
import shutil
import unicodedata
from pathlib import Path

IMAGE_EXTENSIONS = {".png"}

TOP_LEVEL_RULES = {
    "android phone": ("phone", "android", None),
    "android tablet": ("tablet", "android", None),
    "ios": ("phone", "ios", "apple"),
    "ipados": ("tablet", "ipados", "apple"),
    "macbook": ("laptop", "macos", "apple"),
    "mac desktop": ("desktop", "macos", "apple"),
    "windows laptop": ("laptop", "windows", None),
    "windows desktop": ("desktop", "windows", None),
}

MANUFACTURERS = {
    "apple": "apple",
    "google": "google",
    "pixel": "google",
    "samsung": "samsung",
    "dell": "dell",
    "lenovo": "lenovo",
    "microsoft": "microsoft",
    "huawei": "huawei",
}

def contains_variant_token(text: str, token: str) -> bool:
    """Return True if the filename contains the given variant token."""
    parts = [clean(p) for p in re.split(r"\s*-\s*", text) if clean(p)]
    target = slugify(token)
    for part in parts:
        key = slugify(part)
        if key == target:
            return True
    return False


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s.-]", " ", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def ensure_unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def infer_manufacturer(*texts: str, fallback: str | None = None) -> str:
    haystack = " ".join(texts).lower()
    for key, value in MANUFACTURERS.items():
        if re.search(rf"\b{re.escape(key)}\b", haystack):
            return value
    return fallback or "unknown"


def strip_known_suffix_tokens(text: str) -> str:
    text = clean(text)

    parts = [clean(p) for p in re.split(r"\s*-\s*", text) if clean(p)]
    if len(parts) <= 1:
        return text
    return clean(parts[0])


def crop_empty_pixels(src: Path, dst: Path, rotate_degrees: int = 0) -> None:
    try:
        from PIL import Image, ImageChops  # type: ignore[import-not-found]
    except ImportError:
        logging.error("Pillow not installed; copying without crop: %s", src)
        shutil.copy2(src, dst)
        return

    try:
        with Image.open(src) as img:
            img.load()
            if rotate_degrees:
                img = img.rotate(rotate_degrees, expand=True)

            if "A" in img.getbands() or "transparency" in img.info:
                alpha = img.getchannel("A") if "A" in img.getbands() else img.split()[-1]
                bbox = alpha.getbbox()
            else:
                bg = Image.new(img.mode, img.size, img.getpixel((0, 0)))
                diff = ImageChops.difference(img, bg)
                bbox = diff.getbbox()

            if not bbox or bbox == (0, 0, img.width, img.height):
                shutil.copy2(src, dst)
                return

            cropped = img.crop(bbox)
            cropped.save(dst, format=img.format)
    except OSError as exc:
        logging.error("Failed to crop %s: %s", src, exc)
        shutil.copy2(src, dst)


def split_chip_models(model: str) -> list[str]:
    """
    iPad Pro 11 M4 & M5 -> [iPad Pro 11 M4, iPad Pro 11 M5]
    iPad Air 13 M2 & M3 -> [iPad Air 13 M2, iPad Air 13 M3]
    """
    m = re.search(r"^(.*)\s(M\d+)\s*&\s*(M\d+)$", clean(model))
    if not m:
        return [clean(model)]

    base, chip1, chip2 = m.groups()
    return [f"{base} {chip1}", f"{base} {chip2}"]


def build_variant_filename(stem: str, model_name: str | None = None) -> str:
    """
    Extract only the variant tokens from the filename.
    Example:
      '16 - Teal' -> 'teal.png'
      'iPad Pro 11 M4 & M5 - Landscape - Space Black' -> 'landscape-space-black.png'
      'Studio Display' -> 'default.png'
    """
    parts = [clean(p) for p in re.split(r"\s*-\s*", stem) if clean(p)]

    if len(parts) <= 1:
        if model_name:
            model_name = clean(model_name)
            pattern = rf"^\s*{re.escape(model_name)}\s*"
            remainder = re.sub(pattern, "", stem, flags=re.IGNORECASE).strip()
            remainder = re.sub(r"\blandscape\b", "", remainder, flags=re.IGNORECASE).strip()
            if remainder:
                return f"{slugify(remainder)}.png"
        return "default.png"

    tokens: list[str] = []
    for part in parts[1:]:
        token = slugify(part)
        if token in {"portrait", "landscape"}:
            continue
        if token:
            tokens.append(token)

    if not tokens:
        return "default.png"

    # dedupe but preserve order
    seen = set()
    unique_tokens = []
    for token in tokens:
        if token not in seen:
            unique_tokens.append(token)
            seen.add(token)

    return f"{'-'.join(unique_tokens)}.png"


def infer_ios_model(folder_name: str) -> str:
    folder_name = clean(folder_name)
    lower = folder_name.lower()

    if lower.startswith("iphone "):
        return folder_name
    if lower == "air":
        return "iPhone Air"
    if re.match(r"^\d", folder_name):
        return f"iPhone {folder_name}"
    return folder_name


def infer_ipad_model(rel_parts: tuple[str, ...], file_stem: str) -> str:
    """
    Relative parts below Exports:
      iPadOS / iPad Air / M2 & M3 / 11 / file.png
      iPadOS / iPad mini / A17 Pro / file.png
    """
    segments = list(rel_parts[1:-1])  # drop top-level folder and filename

    family = segments[0] if len(segments) >= 1 else "iPad"
    chip = segments[1] if len(segments) >= 2 else None
    size = segments[2] if len(segments) >= 3 and re.match(r"^\d", segments[2]) else None

    if family.lower() == "ipad mini" and not size:
        m = re.search(r"\b(\d+(?:\.\d+)?)\b", file_stem)
        if m:
            size = m.group(1)

    parts = [family]
    if size:
        parts.append(size)
    if chip:
        parts.append(chip)

    return clean(" ".join(parts))


def infer_model_and_manufacturer(rel_path: Path) -> tuple[str, str, str, str]:
    """
    Returns:
      device_type, os_name, manufacturer, model
    """
    parts = rel_path.parts
    top = parts[0]
    top_key = top.lower()

    device_type, os_name, default_manufacturer = TOP_LEVEL_RULES.get(
        top_key, ("unknown", "unknown", None)
    )

    parent = rel_path.parent.name
    stem = rel_path.stem

    if top_key == "ios":
        manufacturer = "apple"
        model = infer_ios_model(parent)

    elif top_key == "ipados":
        manufacturer = "apple"
        model = infer_ipad_model(parts, stem)

    elif top_key == "android phone":
        manufacturer = infer_manufacturer(parent, stem, fallback=default_manufacturer)
        model = parent

    elif top_key == "android tablet":
        manufacturer = infer_manufacturer(parent, stem, fallback=default_manufacturer)
        model = parent

    elif top_key == "macbook":
        manufacturer = "apple"
        model = strip_known_suffix_tokens(stem)

    elif top_key == "mac desktop":
        manufacturer = "apple"
        model = parent if parent.lower() != "mac desktop" else strip_known_suffix_tokens(stem)

    elif top_key == "windows laptop":
        # e.g. Windows Laptop / Dell / 2024 XPS 14 Platinum.png
        manufacturer_folder = parts[1] if len(parts) >= 3 else ""
        manufacturer = infer_manufacturer(manufacturer_folder, stem, fallback=default_manufacturer)
        model = strip_known_suffix_tokens(stem)

    elif top_key == "windows desktop":
        manufacturer = infer_manufacturer(stem, fallback=default_manufacturer)
        model = strip_known_suffix_tokens(stem)
        if manufacturer != "unknown":
            model = re.sub(
                rf"^\s*{re.escape(manufacturer)}\s+",
                "",
                model,
                flags=re.IGNORECASE,
            ).strip()

    else:
        manufacturer = infer_manufacturer(parent, stem, fallback=default_manufacturer)
        model = strip_known_suffix_tokens(parent)

    return device_type, os_name, manufacturer, clean(model)


def process_file(
    src_root: Path,
    file_path: Path,
    dst_root: Path,
    dry: bool,
    verbose: bool,
    ignore_landscape: bool,
) -> int:
    if file_path.name == ".DS_Store":
        # logging.warn("%s - Skipped .DS_Store", file_path)
        return 0
    if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
        logging.warning("%s - Skipped non-image", file_path)
        return 0
    if re.search(r"\b(wallpapers|menu-bar|menu bar?)\b", str(file_path), flags=re.IGNORECASE):
        logging.info("%s - Skipped path", file_path)
        return 0
    has_keyword_variant = bool(
        re.search(r"\b(pencil|shadow|wallpapers?)\b", file_path.stem, flags=re.IGNORECASE)
    )
    
    if has_keyword_variant:
        logging.info("%s - Skipped keyword variant (exists)", file_path)
        return 0
    
    is_landscape = contains_variant_token(file_path.stem, "landscape")
    if ignore_landscape and is_landscape:
        logging.info("%s - Rotating landscape variant", file_path)

    rel_path = file_path.relative_to(src_root)
    device_type, os_name, manufacturer, model = infer_model_and_manufacturer(rel_path)
    models = split_chip_models(model)
    variant_name = build_variant_filename(file_path.stem, model)
    # Manual reformatting
    variant_name = re.sub(r"m2-and-m3-", "", variant_name, flags=re.IGNORECASE)
    variant_name = re.sub(r"8\.3-a17-pro-", "", variant_name, flags=re.IGNORECASE)
    # If variant_name matches model (full or partly), use 'default.png'
    model_slug = slugify(model)
    variant_slug = variant_name.replace('.png', '')
    if variant_slug == model_slug or model_slug in variant_slug or variant_slug in model_slug:
        variant_name = 'default.png'

    copies = 0
    for model_name in models:
        model_clean = model_name
        manufacturer_slug = slugify(manufacturer)
        model_slug = slugify(model_clean)
        # Remove manufacturer from model if present
        if model_slug.startswith(manufacturer_slug):
            model_clean = model_clean[len(manufacturer):].lstrip()
            # Remove leading spaces and punctuation
            model_clean = re.sub(r'^[\s\-]+', '', model_clean)
        dst_dir = dst_root / device_type / os_name / manufacturer / model_clean
        dst_file = ensure_unique(dst_dir / variant_name)

        if dst_file.exists():
            logging.info("%s - Skipped keyword variant (exists)", file_path)
            continue

        if verbose or dry:
            logging.info("%s -> %s", file_path, dst_file)

        try:
            if not dry:
                dst_dir.mkdir(parents=True, exist_ok=True)
                rotate_degrees = 270 if is_landscape else 0
                crop_empty_pixels(file_path, dst_file, rotate_degrees=rotate_degrees)
                if is_landscape and dst_file.exists():
                    try:
                        file_path.unlink()
                        logging.info("Deleted original after rotation: %s", file_path)
                    except OSError as exc:
                        logging.error("Failed to delete original: %s", exc)
            copies += 1
        except OSError as exc:
            logging.error("Failed to copy %s -> %s: %s", file_path, dst_file, exc)

    return copies


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy device frame files into a normalized folder structure."
    )
    parser.add_argument("source", type=Path, help="Source folder, e.g. ./Exports")
    parser.add_argument("destination", type=Path, help="Destination folder, e.g. ./organized")
    parser.add_argument(
        "--dry",
        type=int,
        choices=[0, 1],
        default=1,
        help="1 = simulate only, 0 = actually copy files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each output path",
    )
    args = parser.parse_args()

    logging.basicConfig(
      level=logging.DEBUG if args.verbose else logging.INFO,
      format="[%(levelname)-7s] %(message)s",
    )

    src_root = args.source.resolve()
    dst_root = args.destination.resolve()
    dry = bool(args.dry)
    ignore_landscape = True

    if not src_root.exists() or not src_root.is_dir():
        print(f"Invalid source directory: {src_root}")
        return 1

    copied = 0
    scanned = 0

    for file_path in src_root.rglob("*"):
        if not file_path.is_file():
            continue
        scanned += 1
        copied += process_file(
            src_root,
            file_path,
            dst_root,
            dry=dry,
            verbose=args.verbose,
            ignore_landscape=ignore_landscape,
        )

    print(f"Done. scanned={scanned} copied={copied} dry={int(dry)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())