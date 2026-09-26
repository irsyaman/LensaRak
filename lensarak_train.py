#!/usr/bin/env python3
"""
LensaRak — quick trainer: pulls a YOLOv8-format dataset straight from your
Roboflow project and trains a model on it, even if you've only labeled a
handful of images so far. Good for proving the real pipeline (real
item_code classes, not generic COCO "person"/"chair") works end-to-end
BEFORE the full 109-item dataset is done — you'll just retrain later with
more data by bumping --version after generating a new Version in Roboflow.

SETUP
-----
    pip install roboflow ultralytics

WHERE TO GET THE VALUES BELOW
------------------------------
In Roboflow: your project -> Generate -> (create/pick a Version) -> Export
-> Show download code (pick "YOLOv8"). It gives you a snippet like:

    from roboflow import Roboflow
    rf = Roboflow(api_key="AbCdEfGh12345")
    project = rf.workspace("your-workspace").project("lensarak-xxxxx")
    version = project.version(1)
    dataset = version.download("yolov8")

Copy the 4 values (api_key, workspace, project, version number) into the
command below.

USAGE
-----
    python lensarak_train.py --api-key AbCdEfGh12345 --workspace your-workspace \\
        --project lensarak-xxxxx --version 1 --epochs 50

WHAT HAPPENS
------------
1. Downloads the labeled dataset from Roboflow (./lensarak-dataset-v<N>/)
2. Trains a YOLOv8 model on it (starts from yolov8n.pt — fast, good enough
   for a first test; swap --base-model to yolov8s.pt later for accuracy)
3. Copies the trained weights to best.pt right next to lensarak_vision.py
   -- the moment that file exists, lensarak_vision.py automatically stops
   using the generic COCO fallback and uses YOUR model instead. No code
   change needed on that end.

A tiny/partial dataset will NOT be accurate yet -- that's expected. The
point of this run is to prove item_code -> detection -> ledger works with
your REAL classes. Re-run this script (bump --version) once more of the
109 items are labeled.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="LensaRak — train from a Roboflow export")
    parser.add_argument("--api-key", required=True, help="Roboflow API key (from Export -> Show download code)")
    parser.add_argument("--workspace", required=True, help="Roboflow workspace slug")
    parser.add_argument("--project", required=True, help="Roboflow project slug")
    parser.add_argument("--version", type=int, required=True, help="Dataset Version number to pull")
    parser.add_argument("--epochs", type=int, default=50, help="Training epochs (default 50 — fine for a quick test)")
    parser.add_argument("--imgsz", type=int, default=640, help="Training image size (default 640)")
    parser.add_argument("--base-model", default="yolov8n.pt",
                         help="Starting weights (default yolov8n.pt — fastest; try yolov8s.pt for more accuracy once you have more data)")
    args = parser.parse_args()

    try:
        from roboflow import Roboflow
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install roboflow ultralytics")
    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("Missing dependency. Run:  pip install roboflow ultralytics")

    print(f"Downloading dataset: workspace={args.workspace} project={args.project} version={args.version} ...")
    rf = Roboflow(api_key=args.api_key)
    project = rf.workspace(args.workspace).project(args.project)
    version = project.version(args.version)
    dataset = version.download("yolov8")
    data_yaml = Path(dataset.location) / "data.yaml"
    if not data_yaml.exists():
        sys.exit(f"Expected {data_yaml} after download but it's missing — check the dataset export format was YOLOv8.")
    print(f"Dataset ready at: {dataset.location}")

    print(f"\nTraining {args.base_model} for {args.epochs} epochs (imgsz={args.imgsz}) ...")
    print("(Small dataset + CPU-only laptop = this can take a while. A free Google Colab GPU "
          "runtime is much faster if your laptop doesn't have one — same script works there too.)")
    model = YOLO(args.base_model)
    results = model.train(data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz)

    trained_weights = Path(results.save_dir) / "weights" / "best.pt"
    if not trained_weights.exists():
        sys.exit(f"Training finished but couldn't find weights at {trained_weights} — check the run's output above for errors.")

    target = HERE / "best.pt"
    shutil.copy2(trained_weights, target)
    print(f"\nDone. Copied trained weights -> {target}")
    print("lensarak_vision.py will automatically use this model on its next run "
          "(TEST MODE message will disappear).")
    print(f"\nClasses this model knows: {model.names}")
    print("^ these should be your item_code values (E001, T009, ...). If you see something else, "
          "double-check your Roboflow classes were named as item_codes when you labeled.")


if __name__ == "__main__":
    main()
