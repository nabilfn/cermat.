"use client";

import type { ReactNode } from "react";
import type { ReconciliationIssue } from "../../lib/types";
import { ConfidenceLookup, EvidenceList } from "./Evidence";

/** Expected / actual / difference, explanation and evidence for one exception. */
export function IssueCard({
  issue,
  lookup,
  status,
  children,
}: {
  issue: ReconciliationIssue;
  lookup?: ConfidenceLookup;
  status?: "open" | "resolved";
  children?: ReactNode;
}) {
  return (
    <article className={`issueCard ${status ? `reviewIssueCard ${status}` : ""}`}>
      <div className="issueTopline">
        <span className={`severity ${issue.severity}`}>{issue.severity}</span>
        <span>{issue.code.replaceAll("_", " ")}</span>
        {status && <span className={`issueState ${status}`}>{status}</span>}
      </div>
      <h3>{issue.title}</h3>
      {issue.item_description && <p className="issueItem">{issue.item_description}</p>}
      {(issue.expected || issue.actual || issue.delta) && (
        <dl className="varianceGrid">
          <div>
            <dt>Expected</dt>
            <dd>{issue.expected ?? "—"}</dd>
          </div>
          <div>
            <dt>Actual</dt>
            <dd>{issue.actual ?? "—"}</dd>
          </div>
          <div>
            <dt>Difference</dt>
            <dd>{issue.delta ?? "—"}</dd>
          </div>
        </dl>
      )}
      <p className="issueExplanation">{issue.explanation}</p>
      <EvidenceList sources={issue.sources} lookup={lookup} />
      {children}
    </article>
  );
}
