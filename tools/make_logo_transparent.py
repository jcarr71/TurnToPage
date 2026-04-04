from __future__ import annotations

from pathlib import Path
import sys

try:
    from PIL import Image
except Exception:
    raise SystemExit("Pillow is required. Install with: python -m pip install Pillow")


def make_transparent(in_path: Path, out_path: Path, width: int = 260, threshold: int = 12) -> None:
    img = Image.open(in_path).convert("RGBA")
    # Make a copy and convert near-black to transparent
    datas = img.getdata()
    new_data = []
    for item in datas:
        r, g, b, a = item
        if a == 0:
            new_data.append((r, g, b, a))
            continue
        if r <= threshold and g <= threshold and b <= threshold:
            new_data.append((r, g, b, 0))
        else:
            new_data.append((r, g, b, a))

    img.putdata(new_data)

    # Resize preserving aspect ratio
    wpercent = width / float(img.size[0])
    hsize = int((float(img.size[1]) * float(wpercent)))
    img = img.resize((width, hsize), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    in_file = Path("images/logo.png")
    out_file = Path("images/logo_transparent.png")
    width = 260
    if len(argv) >= 1:
        in_file = Path(argv[0])
    if len(argv) >= 2:
        out_file = Path(argv[1])
    if len(argv) >= 3:
        width = int(argv[2])

    if not in_file.exists():
        print(f"Input file not found: {in_file}")
        return 2

    make_transparent(in_file, out_file, width=width)
    print(f"Wrote: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
