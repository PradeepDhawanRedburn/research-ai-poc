"""Pipeline A+: MarkItDown as the chassis, with the research DOCX plugin doing the Word step.

Same CLI shape as pipeline A. Output goes to out/A+/<doc>.md plus a canonical.json per report.
"""
from __future__ import annotations

import json
import os
import sys
import time

from markitdown import MarkItDown

from research_ingest.markitdown_plugin import ResearchDocxConverter


def main(files: list[str], out_dir: str = "out/A+") -> int:
    os.makedirs(out_dir, exist_ok=True)
    md = MarkItDown()
    conv = ResearchDocxConverter()
    md.register_converter(conv)  # same as enable_plugins=True once the package is installed
    report = []
    for f in files:
        doc_id = os.path.splitext(os.path.basename(f))[0]
        t0 = time.time()
        result = md.convert(f, assets_dir=os.path.join(out_dir, doc_id, "assets"))
        text = result.markdown
        with open(os.path.join(out_dir, f"{doc_id}.md"), "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(os.path.join(out_dir, doc_id, "canonical.json"), "w", encoding="utf-8") as fh:
            json.dump(conv.last_document, fh, ensure_ascii=False, indent=1)
        row = {"doc": doc_id, "seconds": round(time.time() - t0, 1), "chars": len(text), "title": result.title}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
