"""The store: one container holds everything (see docs/DESIGN.md §3.1).

`Store` is the only way code touches documents, assets, facts, chunks and evaluation runs. Two implementations with
the same API: `BlobStore` (Azure Blob Storage, Entra ID or connection string) and `LocalStore` (a folder, for
development and tests). `open_store()` picks one from the settings. Keys are forward-slash paths such as
`markdown/RA02752.md`; never store a local path anywhere else.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import settings

# key prefixes, so every module spells them the same way
RAW = "raw"
MARKDOWN = "markdown"
ASSETS = "assets"
FACTS = "facts"
CHUNKS = "chunks"
DESCRIPTIONS = "descriptions"
EVAL = "eval"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Entry:
    key: str
    size: int
    modified: datetime | None = None


class Store:
    """Interface. Bytes in, bytes out; helpers for text and JSON."""

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def list(self, prefix: str = "") -> list[Entry]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    # --- helpers shared by both implementations

    def text(self, key: str) -> str:
        return self.get(key).decode("utf-8")

    def json(self, key: str, default=None):
        if default is not None and not self.exists(key):
            return default
        return json.loads(self.text(key))

    def put_json(self, key: str, obj) -> None:
        self.put(key, json.dumps(obj, ensure_ascii=False, indent=1), "application/json")

    def jsonl(self, key: str) -> list[dict]:
        return [json.loads(l) for l in self.text(key).splitlines() if l.strip()]

    def put_jsonl(self, key: str, rows: Iterable[dict]) -> None:
        self.put(key, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "application/x-ndjson")

    def keys(self, prefix: str = "", suffix: str = "") -> list[str]:
        return [e.key for e in self.list(prefix) if e.key.endswith(suffix)]

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for e in self.list(prefix):
            self.delete(e.key)
            n += 1
        return n

    def local_path(self, key: str) -> Path:
        """A copy on disk for libraries that need a file path (PIL, zipfile). Cached by key under CACHE_DIR."""
        p = settings.cache_dir / "store" / key
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.get(key))
        return p

    def upload_file(self, path: Path, key: str) -> None:
        self.put(key, path.read_bytes(), mimetypes.guess_type(path.name)[0])

    def doc_ids(self) -> list[str]:
        """Reports in the store: one per raw Word file."""
        return sorted(Path(k).stem for k in self.keys(f"{RAW}/", ".docx"))


class LocalStore(Store):
    def __init__(self, root: Path):
        self.root = Path(root).resolve()  # resolved once: Windows short names (PRADEE~1) break relative_to otherwise
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"key escapes the store: {key}")
        return p

    def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)

    def exists(self, key: str) -> bool:
        return self._p(key).is_file()

    def delete(self, key: str) -> None:
        p = self._p(key)
        if p.is_file():
            p.unlink()

    def list(self, prefix: str = "") -> list[Entry]:
        base = self._p(prefix) if prefix else self.root
        if base.is_file():
            return [Entry(prefix, base.stat().st_size, datetime.fromtimestamp(base.stat().st_mtime, timezone.utc))]
        if not base.is_dir():
            # prefix may be a partial file name, e.g. "eval/2026-10"
            parent, stem = base.parent, base.name
            return sorted((Entry(p.relative_to(self.root).as_posix(), p.stat().st_size,
                                 datetime.fromtimestamp(p.stat().st_mtime, timezone.utc))
                           for p in parent.rglob("*") if p.is_file() and p.name.startswith(stem)
                           and p.relative_to(self.root).as_posix().startswith(prefix)), key=lambda e: e.key)
        return sorted((Entry(p.relative_to(self.root).as_posix(), p.stat().st_size,
                             datetime.fromtimestamp(p.stat().st_mtime, timezone.utc))
                       for p in base.rglob("*") if p.is_file()), key=lambda e: e.key)

    def describe(self) -> str:
        return f"local folder {self.root}"

    def wipe(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


class BlobStore(Store):
    def __init__(self, container: str, account_url: str = "", connection_string: str = ""):
        from azure.storage.blob import BlobServiceClient

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential(process_timeout=60))
        self.account_url = account_url or service.url
        self.container_name = container
        self.container = service.get_container_client(container)
        if not self.container.exists():
            self.container.create_container()

    def get(self, key: str) -> bytes:
        return self.container.download_blob(key).readall()

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        from azure.storage.blob import ContentSettings

        payload = data.encode("utf-8") if isinstance(data, str) else data
        self.container.upload_blob(key, payload, overwrite=True,
                                   content_settings=ContentSettings(content_type=content_type) if content_type else None)

    def exists(self, key: str) -> bool:
        return self.container.get_blob_client(key).exists()

    def delete(self, key: str) -> None:
        self.container.delete_blob(key)

    def list(self, prefix: str = "") -> list[Entry]:
        return [Entry(b.name, b.size, b.last_modified) for b in self.container.list_blobs(name_starts_with=prefix)]

    def describe(self) -> str:
        return f"{self.account_url}/{self.container_name}"

    def blob_url(self, key: str) -> str:
        return f"{self.account_url}/{self.container_name}/{key}"


_store: Store | None = None


def open_store() -> Store:
    """The configured store, created once per process."""
    global _store
    if _store is None:
        if settings.store_local_dir:
            _store = LocalStore(Path(settings.store_local_dir))
        else:
            settings.require("storage_account_url") if not settings.storage_connection_string else None
            _store = BlobStore(settings.storage_container, settings.storage_account_url,
                               settings.storage_connection_string)
    return _store


def set_store(store: Store) -> None:
    """For tests."""
    global _store
    _store = store


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def asset_key(doc_id: str, sha: str) -> str:
    return f"{ASSETS}/{doc_id}/{sha}.png"
