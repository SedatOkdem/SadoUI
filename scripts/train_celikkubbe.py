"""Train YOLOv11 on the Celik Kubbe Roboflow dataset and export GCS weights.

Usage (from repo root):
    python scripts/train_celikkubbe.py
    python scripts/train_celikkubbe.py --model yolo11s.pt --epochs 80 --batch 32
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "datasets" / "aeroshield-merged" / "data.yaml"
DEFAULT_OUT = ROOT / "models" / "aeroshield.pt"


def _with_absolute_path(data_yaml: Path) -> Path:
    """Ultralytics treats path: . as CWD; pin to the yaml directory."""
    import yaml

    data_yaml = data_yaml.resolve()
    cfg = yaml.safe_load(data_yaml.read_text(encoding="utf-8")) or {}
    cfg["path"] = str(data_yaml.parent).replace("\\", "/")
    data_yaml.write_text(
        "# Auto-patched path for Ultralytics\n" + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return data_yaml


def main() -> int:
    parser = argparse.ArgumentParser(description="Train AeroShield YOLOv11 on Celik Kubbe v8")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", default="yolo11n.pt", help="Base Ultralytics weights (yolo11n/s/m.pt)")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--device", default="", help='"" auto, "0" CUDA, "cpu"')
    parser.add_argument("--name", default="aeroshield")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.data.exists():
        print(f"Dataset yaml bulunamadı: {args.data}", file=sys.stderr)
        print("Zip'i datasets/celik-kubbe-v8 altına açın.", file=sys.stderr)
        return 1

    data_yaml = _with_absolute_path(args.data)

    from ultralytics import YOLO

    model = YOLO(args.model)
    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device or None,
        project=str(ROOT / "runs" / "detect"),
        name=args.name,
        exist_ok=True,
        patience=15,
        workers=0 if sys.platform.startswith("win") else 4,
        pretrained=True,
        plots=False,
        cache=False,
    )

    best = Path(getattr(results, "save_dir", ROOT / "runs" / "detect" / args.name)) / "weights" / "best.pt"
    if not best.exists():
        # Ultralytics 8.x: results.save_dir is a Path-like
        save_dir = Path(str(results.save_dir)) if hasattr(results, "save_dir") else best.parent.parent
        best = save_dir / "weights" / "best.pt"
    if not best.exists():
        print(f"best.pt bulunamadı: {best}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, args.out)
    print(f"GCS ağırlığı kopyalandı: {args.out}")
    print("config.yaml → model.path: models/aeroshield.pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
