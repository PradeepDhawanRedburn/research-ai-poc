"""Pipeline B: Azure AI Content Understanding, prebuilt document analyzer.

Runs only when the environment is configured; otherwise writes NOT_RUN.md and exits 0,
so the comparison harness can report the arm as "not run" rather than failing.

Environment:
  AZURE_CU_ENDPOINT      e.g. https://<resource>.services.ai.azure.com
  AZURE_CU_KEY           API key (or set AZURE_CU_BEARER to a bearer token from `az account get-access-token`)
  AZURE_CU_API_VERSION   default 2025-11-01  (verify against the current GA version before relying on it)
  AZURE_CU_ANALYZER      default prebuilt-documentAnalyzer

The REST shape (POST .../analyzers/{id}:analyze, poll Operation-Location, read result.contents[].markdown)
is taken from the public docs and has NOT been exercised from this machine yet.
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests


def main(files: list[str], out_dir: str = "out/B") -> int:
    os.makedirs(out_dir, exist_ok=True)
    endpoint = os.environ.get("AZURE_CU_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AZURE_CU_KEY")
    bearer = os.environ.get("AZURE_CU_BEARER")
    if not endpoint or not (key or bearer):
        with open(os.path.join(out_dir, "NOT_RUN.md"), "w", encoding="utf-8") as fh:
            fh.write("Pipeline B (Azure Content Understanding) was not run: AZURE_CU_ENDPOINT and a credential are not set.\n")
        print("pipeline B skipped: no Azure Content Understanding configuration")
        return 0
    api = os.environ.get("AZURE_CU_API_VERSION", "2025-11-01")
    analyzer = os.environ.get("AZURE_CU_ANALYZER", "prebuilt-documentAnalyzer")
    headers = {"Content-Type": "application/octet-stream"}
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    else:
        headers["Authorization"] = f"Bearer {bearer}"
    report = []
    for f in files:
        doc_id = os.path.splitext(os.path.basename(f))[0]
        t0 = time.time()
        with open(f, "rb") as fh:
            r = requests.post(f"{endpoint}/contentunderstanding/analyzers/{analyzer}:analyze?api-version={api}",
                              headers=headers, data=fh.read(), timeout=120)
        r.raise_for_status()
        op = r.headers.get("Operation-Location")
        result = None
        for _ in range(240):
            time.sleep(3)
            pr = requests.get(op, headers={k: v for k, v in headers.items() if k != "Content-Type"}, timeout=60)
            pr.raise_for_status()
            body = pr.json()
            if body.get("status", "").lower() in ("succeeded", "failed"):
                result = body
                break
        if not result or result.get("status", "").lower() != "succeeded":
            print(f"{doc_id}: analysis did not succeed: {result and result.get('status')}")
            continue
        with open(os.path.join(out_dir, f"{doc_id}.result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
        contents = result.get("result", {}).get("contents", [])
        md = "\n\n".join(c.get("markdown", "") for c in contents)
        with open(os.path.join(out_dir, f"{doc_id}.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        row = {"doc": doc_id, "seconds": round(time.time() - t0, 1), "chars": len(md),
               "figures": sum(len(c.get("figures", [])) for c in contents),
               "tables": sum(len(c.get("tables", [])) for c in contents)}
        report.append(row)
        print(json.dumps(row))
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
