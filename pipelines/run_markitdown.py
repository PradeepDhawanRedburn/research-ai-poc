"""Pipeline A: plain Markdown conversion with Microsoft MarkItDown (the original proposal).

Writes one .md per report plus a sidecar JSON with sizes. Base64 data URIs (inline
images) are stripped into a second file so that token counts are comparable with the
other pipelines; the raw output is kept as well.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

from markitdown import MarkItDown

DATA_URI = re.compile(r"!\[([^\]]*)\]\(data:[^)]+\)")


def main(files: list[str], out_dir: str = "out/A") -> int:
    os.makedirs(out_dir, exist_ok=True)
    md = MarkItDown()
    report = []
    for f in files:
        doc_id = os.path.splitext(os.path.basename(f))[0]
        t0 = time.time()
        result = md.convert(f)
        text = result.text_content or ""
        stripped, n_images = DATA_URI.subn(r"![\1](image)", text)
        with open(os.path.join(out_dir, f"{doc_id}.raw.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(os.path.join(out_dir, f"{doc_id}.md"), "w", encoding="utf-8") as fh:
            fh.write(stripped)
        row = {"doc": doc_id, "seconds": round(time.time() - t0, 1), "raw_chars": len(text),
               "chars_without_data_uris": len(stripped), "inline_images": n_images,
               "pipe_table_rows": sum(1 for ln in stripped.splitlines() if ln.startswith("|")),
               "lines": stripped.count("\n")}
        report.append(row)
        print(json.dumps(row))
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
