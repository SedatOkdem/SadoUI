"""Merge Celik Kubbe v8 + asd1dw v3 + Fezatech into one YOLO dataset with GCS class ids."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "aeroshield-merged"

# Unified GCS ids
# 0 F16, 1 BalistikFuze, 2 Helikopter, 3 MiniIHA, 4 Dost
NAMES = {
    0: "F16",
    1: "BalistikFuze",
    2: "Helikopter",
    3: "MiniIHA",
    4: "Dost",
}

SOURCES = [
    {
        "root": ROOT / "datasets" / "celik-kubbe-v8",
        "prefix": "ck",
        "map": {0: 0, 1: 1, 2: 2, 3: 3},  # already GCS
    },
    {
        "root": ROOT / "datasets" / "asd1dw-v3",
        "prefix": "asd",
        # Roboflow: Dost, Drone, F16, Fuze, Helikopter, iha
        "map": {0: 4, 1: 3, 2: 0, 3: 1, 4: 2, 5: 3},
    },
    {
        "root": ROOT / "datasets" / "fezatech-yaz-v1",
        "prefix": "fez",
        # dost_* → Dost; dusman_drone/f16/fuze/helikopter → MiniIHA/F16/Fuze/Heli
        "map": {0: 4, 1: 4, 2: 4, 3: 4, 4: 3, 5: 0, 6: 1, 7: 2},
    },
]


def _remap_label_file(src: Path, dst: Path, id_map: dict[int, int]) -> None:
    lines_out = []
    text = src.read_text(encoding="utf-8", errors="replace").strip()
    if text:
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) < 5:
                continue
            old = int(float(parts[0]))
            if old not in id_map:
                continue
            parts[0] = str(id_map[old])
            lines_out.append(" ".join(parts))
    dst.write_text(("\n".join(lines_out) + ("\n" if lines_out else "")), encoding="utf-8")


def merge() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    counts = {split: 0 for split in ("train", "valid", "test")}
    for split in counts:
        (OUT / split / "images").mkdir(parents=True, exist_ok=True)
        (OUT / split / "labels").mkdir(parents=True, exist_ok=True)

    for src in SOURCES:
        root = Path(src["root"])
        if not root.exists():
            raise FileNotFoundError(root)
        for split in counts:
            img_dir = root / split / "images"
            if not img_dir.exists():
                continue
            for img in img_dir.iterdir():
                if not img.is_file():
                    continue
                stem = img.stem
                label = root / split / "labels" / f"{stem}.txt"
                new_stem = f"{src['prefix']}__{stem}"
                shutil.copy2(img, OUT / split / "images" / f"{new_stem}{img.suffix}")
                dst_lbl = OUT / split / "labels" / f"{new_stem}.txt"
                if label.exists():
                    _remap_label_file(label, dst_lbl, src["map"])
                else:
                    dst_lbl.write_text("", encoding="utf-8")
                counts[split] += 1

    yaml_path = OUT / "data.yaml"
    yaml_path.write_text(
        (
            "# Merged Celik Kubbe v8 + asd1dw v3 + Fezatech yaz v1\n"
            f"path: {str(OUT).replace(chr(92), '/')}\n"
            "train: train/images\n"
            "val: valid/images\n"
            "test: test/images\n"
            f"nc: {len(NAMES)}\n"
            "names:\n"
            + "".join(f"  {k}: {v}\n" for k, v in NAMES.items())
        ),
        encoding="utf-8",
    )
    print("merged", counts, "->", yaml_path)


if __name__ == "__main__":
    merge()
