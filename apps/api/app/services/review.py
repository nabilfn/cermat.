from __future__ import annotations

import hashlib
import json

from app.schemas import ReconciliationIssue


def make_issue_key(issue: ReconciliationIssue) -> str:
    """Return a stable identity for the same business exception across reruns."""
    identity = {
        "code": issue.code,
        "item_description": issue.item_description,
        "expected": issue.expected,
        "actual": issue.actual,
        "sources": [
            {
                "document_type": source.document_type.value,
                "field_path": source.field_path,
            }
            for source in issue.sources
        ],
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
