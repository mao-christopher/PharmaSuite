#!/usr/bin/env python3
"""CLI wrapper around src/pharma/train.py."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pharma.train import train, export


def main():
    parser = argparse.ArgumentParser(description="Fine-tune or export a YOLO model")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("train", help="Fine-tune on a custom dataset")
    t.add_argument("data_yaml", help="Path to dataset YAML")
    t.add_argument("--model", default=None)
    t.add_argument("--epochs", type=int, default=100)
    t.add_argument("--imgsz", type=int, default=640)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--name", default="train")

    e = sub.add_parser("export", help="Export weights to deployment format")
    e.add_argument("weights", help="Path to .pt file")
    e.add_argument("--format", default="onnx", choices=["onnx", "engine", "coreml", "tflite"])

    args = parser.parse_args()

    if args.command == "train":
        train(
            data_yaml=args.data_yaml,
            model_path=args.model,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            name=args.name,
        )
    elif args.command == "export":
        export(weights=args.weights, format=args.format)


if __name__ == "__main__":
    main()
