from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import fitz
from paddleocr import PaddleOCR


MODEL_CONFIGS = {
    "v5-server": {
        "text_detection_model_name": "PP-OCRv5_server_det",
        "text_recognition_model_name": "PP-OCRv5_server_rec",
    },
    "v6-medium": {
        "text_detection_model_name": "PP-OCRv6_medium_det",
        "text_recognition_model_name": "PP-OCRv6_medium_rec",
    },
}


def render_page(pdf_path: Path, physical_page: int, output_path: Path, dpi: int) -> None:
    document = fitz.open(pdf_path)
    if not 1 <= physical_page <= document.page_count:
        raise ValueError(f"Physical page {physical_page} is outside 1..{document.page_count}")
    page = document.load_page(physical_page - 1)
    zoom = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(output_path)
    document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True, help="One-based physical PDF page")
    parser.add_argument("--model", choices=sorted(MODEL_CONFIGS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=240)
    parser.add_argument("--device", default="gpu:0")
    parser.add_argument("--textline-orientation", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"{args.pdf.stem}_physical_page_{args.page:04d}_{args.dpi}dpi.png"
    render_page(args.pdf, args.page, image_path, args.dpi)

    pipeline = PaddleOCR(
        device=args.device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=args.textline_orientation,
        **MODEL_CONFIGS[args.model],
    )
    started = time.perf_counter()
    results = list(pipeline.predict(str(image_path)))
    elapsed = time.perf_counter() - started

    result_dir = output_dir / args.model
    result_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        result.save_to_json(str(result_dir))
        result.save_to_img(str(result_dir))

    manifest = {
        "pdf": str(args.pdf),
        "pdf_physical_page": args.page,
        "render_dpi": args.dpi,
        "rendered_image": str(image_path),
        "model": args.model,
        "model_config": MODEL_CONFIGS[args.model],
        "device": args.device,
        "textline_orientation": args.textline_orientation,
        "elapsed_seconds": round(elapsed, 3),
        "result_count": len(results),
    }
    (result_dir / "benchmark_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
