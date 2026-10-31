"""Access control: who may see which report.

The access list lives in the store as `acl.json`:

    {"default": ["public"], "docs": {"RA02283": ["group:gaming", "user:alice@example.com"]}}

A principal is any string the caller's identity carries ("user:<email>", "group:<name>"); "public" means everyone.
A document absent from `docs` gets `default`. No `acl.json` means every document is public, which is today's
behaviour. The list is copied onto every chunk when it is indexed (`index` / `index --reset`), stored as the
chunk's `acl`, and enforced at query time in both retrieval legs, in the facts tool, and as an OData filter on the
Azure Markdown+ index. Chunks indexed before an `acl.json` existed carry no list and count as public until the
next `index`; `status` says when that is the case.

The caller's principals come from PRINCIPALS in `.env` (comma-separated) or are passed explicitly; no principals
means anonymous, which sees public documents only.
"""
from __future__ import annotations

from .config import settings
from .storage import Store

ACL_KEY = "acl.json"
PUBLIC = "public"


def load(store: Store) -> dict:
    if not store.exists(ACL_KEY):
        return {"default": [PUBLIC], "docs": {}}
    raw = store.json(ACL_KEY) or {}
    default = [str(x) for x in (raw.get("default") or [])]
    docs = {str(k): [str(x) for x in (v or [])] for k, v in (raw.get("docs") or {}).items()}
    return {"default": default, "docs": docs}


def for_doc(acl: dict, doc_id: str) -> list[str]:
    return list(acl["docs"].get(doc_id, acl["default"]))


def allowed(chunk_acl: list[str] | None, principals: list[str] | tuple[str, ...] | None) -> bool:
    """A chunk with no list (indexed before acl.json existed) is public."""
    if not chunk_acl or PUBLIC in chunk_acl:
        return True
    return bool(set(chunk_acl) & set(principals or ()))


def caller_principals(principals=None) -> tuple[str, ...]:
    return tuple(principals) if principals is not None else tuple(settings.principals)


def odata_filter(principals) -> str:
    """Filter for Azure AI Search over the `acl` collection field: public, or any of the caller's principals."""
    values = [PUBLIC] + [p for p in (principals or ()) if p]
    joined = ",".join(v.replace("'", "''") for v in values)
    return f"acl/any(a: search.in(a, '{joined}', ','))"


def unlisted(store: Store, chunks_acl: list[list[str]]) -> bool:
    """True when acl.json exists but indexed chunks carry no list: the index predates it and needs a rebuild."""
    return store.exists(ACL_KEY) and any(not a for a in chunks_acl)
