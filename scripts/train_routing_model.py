"""Train the pulldown routing classifier and export a runtime JSON artifact.

Usage:
    python scripts/train_routing_model.py
    python scripts/train_routing_model.py --manifest scripts/routing_training_manifest.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pulldown.routing import FEATURE_ORDER, LABEL_ORDER, extract_features  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "scripts" / "routing_training_manifest.json",
        help="JSON manifest describing labeled training examples.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "src" / "pulldown" / "routing_model.json",
        help="Destination JSON artifact for the runtime scorer.",
    )
    return parser.parse_args()


def _load_python_globals(path: Path, cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    if path not in cache:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load Python file: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cache[path] = vars(module)
    return cache[path]


def _load_html(entry: dict[str, Any], globals_cache: dict[Path, dict[str, Any]]) -> str:
    if "html_path" in entry:
        path = (ROOT / entry["html_path"]).resolve()
        return path.read_text(encoding="utf-8")

    if "python_file" in entry and "attribute" in entry:
        path = (ROOT / entry["python_file"]).resolve()
        globals_dict = _load_python_globals(path, globals_cache)
        return str(globals_dict[entry["attribute"]])

    raise ValueError(f"Entry must define html_path or python_file+attribute: {entry}")


def _build_dataset(manifest_path: Path) -> tuple[list[list[float]], list[str]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    globals_cache: dict[Path, dict[str, Any]] = {}
    rows: list[list[float]] = []
    labels: list[str] = []

    for entry in manifest:
        label = entry["label"]
        if label not in LABEL_ORDER:
            raise ValueError(f"Unknown label {label!r} in {manifest_path}")

        html = _load_html(entry, globals_cache)
        features = extract_features(html, entry["url"])
        rows.append([float(features[name]) for name in FEATURE_ORDER])
        labels.append(label)

    return rows, labels


def main() -> int:
    args = _parse_args()
    x_rows, y_labels = _build_dataset(args.manifest)

    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x_rows)

    model = LogisticRegression(
        max_iter=4000,
        multi_class="multinomial",
        solver="lbfgs",
        class_weight="balanced",
        random_state=0,
    )
    model.fit(x_scaled, y_labels)

    coef_by_label = {label: row.tolist() for label, row in zip(model.classes_, model.coef_, strict=True)}
    intercept_by_label = {label: float(value) for label, value in zip(model.classes_, model.intercept_, strict=True)}

    payload = {
        "label_order": LABEL_ORDER,
        "feature_order": FEATURE_ORDER,
        "means": scaler.mean_.tolist(),
        "scales": scaler.scale_.tolist(),
        "coef": [coef_by_label.get(label, [0.0] * len(FEATURE_ORDER)) for label in LABEL_ORDER],
        "intercept": [intercept_by_label.get(label, 0.0) for label in LABEL_ORDER],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    counts = Counter(y_labels)
    print(f"trained {len(y_labels)} examples -> {args.output}")
    print("label counts:", ", ".join(f"{label}={counts[label]}" for label in LABEL_ORDER if counts[label]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
