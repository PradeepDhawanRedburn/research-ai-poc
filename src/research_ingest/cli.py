"""Command line: extract .docx files to canonical JSON, rendered text and chunks."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .extract import extract
from .render import build_chunks, render_document, write_chunks


def main(argv=None):
    ap = argparse.ArgumentParser(prog="research-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="extract one or more .docx files")
    ex.add_argument("files", nargs="+")
    ex.add_argument("--out", default="out/C")
    args = ap.parse_args(argv)

    if args.cmd == "extract":
        summary = []
        for f in args.files:
            doc_id = os.path.splitext(os.path.basename(f))[0]
            out_dir = os.path.join(args.out, doc_id)
            os.makedirs(out_dir, exist_ok=True)
            t0 = time.time()
            doc = extract(f, out_dir, doc_id)
            with open(os.path.join(out_dir, "canonical.json"), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=1)
            rendered = render_document(doc)
            with open(os.path.join(out_dir, "rendered.md"), "w", encoding="utf-8") as fh:
                fh.write(rendered)
            chunks = build_chunks(doc)
            write_chunks(chunks, os.path.join(out_dir, "chunks.jsonl"))
            s = doc["stats"]
            summary.append({"doc": doc_id, "seconds": round(time.time() - t0, 1), "elements": s["elements"],
                            "figure_content": s["figure_content_kinds"], "assets": s["assets"], "charts": s["charts"],
                            "rendered_chars": len(rendered), "chunks": len(chunks), "warnings": s["warnings"]})
            print(json.dumps(summary[-1], ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
