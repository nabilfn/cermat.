"use client";

import { workspaceUrl } from "../../lib/api";
import { documentTypeName, fieldLabel } from "../../lib/format";
import type { ContextSource, EvidenceReference } from "../../lib/types";

export type ConfidenceLookup = Map<string, ContextSource>;

export function evidenceKey(documentId: string, fieldPath: string) {
  return `${documentId}:${fieldPath}`;
}

export function confidenceLookup(sources: ContextSource[] | undefined): ConfidenceLookup {
  return new Map((sources ?? []).map((s) => [evidenceKey(s.document_id, s.field_path), s]));
}

const NO_SNIPPET = "No source snippet was captured for this structured value.";

/** Source document, type, page, field, snippet and confidence — never more than one click away. */
export function EvidenceList({
  sources,
  lookup,
  openable = true,
}: {
  sources: EvidenceReference[];
  lookup?: ConfidenceLookup;
  openable?: boolean;
}) {
  if (sources.length === 0) {
    return <p className="evidenceNone">No source evidence was attached to this exception.</p>;
  }
  return (
    <ul className="sourceStack" aria-label="Source evidence">
      {sources.map((source, index) => {
        const extra = lookup?.get(evidenceKey(source.document_id, source.field_path));
        const missing = source.source_text === NO_SNIPPET;
        return (
          <li className={`sourceRow ${missing ? "noSnippet" : ""}`} key={`${source.document_id}-${source.field_path}-${index}`}>
            <span className="sourceType">
              {documentTypeName(source.document_type)}
              <small>{extra?.document_number ?? source.filename}</small>
            </span>
            <span className="sourceQuote">
              {missing ? <em>No source snippet captured — check the original.</em> : `“${source.source_text}”`}
              <small>
                {fieldLabel(source.field_path)}
                {source.value ? ` · extracted ${source.value}` : ""}
              </small>
            </span>
            <span className="sourcePage">
              p.{source.page ?? "—"}
              {extra?.confidence != null && <small>{Math.round(extra.confidence * 100)}% conf.</small>}
            </span>
            {openable && extra?.has_source_file !== false && (
              <a
                className="sourceOpen"
                href={workspaceUrl(`/api/v1/documents/${source.document_id}/file`)}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${source.filename} in a new tab`}
              >
                Open ↗
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );
}
