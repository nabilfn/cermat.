"use client";

import { FormEvent, useState } from "react";
import { api, errorMessage } from "../../lib/api";
import { dateLabel, duration, money, observed, percent, statusClass, statusLabel } from "../../lib/format";
import {
  AnomalySignal,
  BriefResponse,
  FAMILY_LABEL,
  IntelligenceSettings,
  OverviewResponse,
  PERIOD_LABEL,
  Period,
  PriorityItem,
  SupplierDetail,
} from "../../lib/types";
import { useResource } from "../../lib/useResource";
import TrendChart from "./TrendChart";

type OverviewProps = {
  supplierKey: string | null;
  onSupplier: (key: string | null) => void;
  onOpenTransaction: (id: string) => void;
  onAsk: (question: string) => void;
  onStart: (mode: "transaction" | "document") => void;
  onLoadDemo: () => Promise<void>;
  canEditSettings: boolean;
  isDemo: boolean;
};

const BAND_LABEL = { critical: "Critical", high: "High", normal: "Normal" };
const DIRECTION_LABEL = {
  increasing: "Increasing",
  decreasing: "Decreasing",
  stable: "Broadly stable",
};

export default function OverviewWorkspace({
  supplierKey,
  onSupplier,
  onOpenTransaction,
  onAsk,
  onStart,
  onLoadDemo,
  canEditSettings,
  isDemo,
}: OverviewProps) {
  const [period, setPeriod] = useState<Period>("30d");
  const [showSettings, setShowSettings] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [demoState, setDemoState] = useState<{ busy: boolean; error: string }>({ busy: false, error: "" });
  const key = `${period}:${reloadKey}`;
  const overviewResource = useResource<OverviewResponse>(key, (signal) =>
    api<OverviewResponse>(`/api/v1/intelligence/overview?period=${period}`, { signal })
  );
  const briefResource = useResource<BriefResponse>(key, (signal) =>
    api<BriefResponse>(`/api/v1/intelligence/brief?period=${period}`, { signal })
  );
  const overview = overviewResource.data;
  const error = overviewResource.error;
  const loading = overviewResource.loading;
  const brief = briefResource.error ? null : briefResource.data;
  const briefLoading = briefResource.loading;

  async function loadDemo() {
    setDemoState({ busy: true, error: "" });
    try {
      await onLoadDemo();
    } catch (err) {
      setDemoState({ busy: false, error: errorMessage(err, "Could not open the demo workspace.") });
    }
  }

  if (supplierKey) {
    return (
      <SupplierView
        supplierKey={supplierKey}
        onBack={() => onSupplier(null)}
        onOpenTransaction={onOpenTransaction}
        onAsk={onAsk}
      />
    );
  }

  const header = (
    <header className="overviewHeader">
      <div>
        <div className="panelLabel">Operations overview</div>
        <p className="overviewDate">
          {/* Only format once data has loaded on the client — avoids a server/client timezone mismatch. */}
          {overview
            ? new Intl.DateTimeFormat(undefined, {
                day: "2-digit",
                month: "short",
                year: "numeric",
              }).format(new Date(overview.as_of))
            : "\u00a0"}
        </p>
      </div>
      <div className="overviewControls">
        <div className="periodSwitch" role="group" aria-label="Overview period">
          {(Object.keys(PERIOD_LABEL) as Period[]).map((value) => (
            <button
              type="button"
              key={value}
              className={period === value ? "active" : ""}
              aria-pressed={period === value}
              onClick={() => setPeriod(value)}
            >
              {value === "all" ? "All" : value}
            </button>
          ))}
        </div>
        {canEditSettings && (
          <button
            type="button"
            className="askTextButton"
            aria-expanded={showSettings}
            onClick={() => setShowSettings((current) => !current)}
          >
            Thresholds
          </button>
        )}
      </div>
    </header>
  );

  if (error) {
    return (
      <section className="panel overviewPanel">
        {header}
        <div className="askError" role="alert">
          <span>Overview unavailable</span>
          {error}
        </div>
      </section>
    );
  }

  if (!overview) {
    return (
      <section className="panel overviewPanel">
        {header}
        <p className="overviewLoading">Checking workspace…</p>
      </section>
    );
  }

  if (!overview.has_data) {
    return (
      <section className="panel overviewPanel">
        {header}
        <div className="onboarding">
          <p className="onboardingBrand">cermat.</p>
          <h2>Turn purchasing documents into evidence-backed operational decisions.</h2>
          <ol className="onboardingSteps">
            <li><span>1</span>Upload documents</li>
            <li><span>2</span>Compare the transaction</li>
            <li><span>3</span>Review exceptions</li>
            <li><span>4</span>Ask cermat. what needs attention</li>
          </ol>
          <p className="onboardingNote">
            This overview stays empty until there are real, reconciled records — cermat. never fills it with
            sample figures.
          </p>
          <div className="onboardingActions">
            <button type="button" onClick={() => onStart("transaction")}>
              Start with a document
            </button>
            {!isDemo && (
              <button type="button" className="secondaryButton" onClick={() => void loadDemo()} disabled={demoState.busy}>
                {demoState.busy ? "Preparing demo workspace…" : "Load demo workspace"}
              </button>
            )}
          </div>
          {demoState.error && <p className="error" role="alert">{demoState.error}</p>}
          {!isDemo && (
            <p className="overviewFootnote">
              The demo opens in a separate workspace labelled DEMO DATA. Your workspace is never modified.
            </p>
          )}
        </div>
      </section>
    );
  }

  const m = overview.metrics;
  const trend = overview.trend;

  return (
    <section className={`panel overviewPanel ${loading ? "refreshing" : ""}`} aria-busy={loading}>
      {header}

      {showSettings && (
        <ThresholdSettings
          onSaved={() => {
            setShowSettings(false);
            setReloadKey((key) => key + 1);
          }}
        />
      )}

      <div className="overviewGrid">
        <section className="ovAttention" aria-labelledby="ov-attention">
          <div className="sectionTitleRow">
            <span id="ov-attention">Needs attention</span>
            <span>now</span>
          </div>
          <div className="attentionFigures">
            <div className="heroFigure">
              <strong>{m.open_issue_count}</strong>
              <span>open exception{m.open_issue_count === 1 ? "" : "s"}</span>
            </div>
            <dl>
              <div>
                <dt>High severity</dt>
                <dd>{m.high_severity_issue_count}</dd>
              </div>
              <div>
                <dt>Affected transactions</dt>
                <dd>
                  {m.transactions_needing_review}
                  <small> of {m.total_transactions}</small>
                </dd>
              </div>
              <div>
                <dt>Active billed variance</dt>
                <dd className="varianceList">
                  {m.total_active_variance_amount.length === 0
                    ? "—"
                    : m.total_active_variance_amount.map((total) => (
                        <span key={total.currency ?? "none"}>
                          {money(total.currency, total.absolute_total)}
                        </span>
                      ))}
                </dd>
              </div>
              <div>
                <dt>Past review target</dt>
                <dd>{m.overdue_issue_count}</dd>
              </div>
            </dl>
          </div>
          {!overview.has_reconciled_data && (
            <p className="overviewNote">
              No transaction has been reconciled yet — exceptions appear after a three-way match.
            </p>
          )}
        </section>

        <section className="ovPriority" aria-labelledby="ov-priority">
          <div className="sectionTitleRow">
            <span id="ov-priority">Priority queue</span>
            <span>{overview.priority.length}</span>
          </div>
          {overview.priority.length === 0 ? (
            <p className="overviewEmpty">No open exceptions. Nothing to prioritise.</p>
          ) : (
            overview.priority.map((item) => (
              <PriorityRow
                key={item.issue_id}
                item={item}
                onOpenTransaction={onOpenTransaction}
                onSupplier={onSupplier}
              />
            ))
          )}
          <p className="overviewFootnote">
            Ranked by a transparent points score — severity, billed variance, age,
            related issues, recurring supplier patterns and issue type. Not a
            machine-learning risk score.
          </p>
        </section>

        <section className="ovBrief" aria-labelledby="ov-brief">
          <div className="sectionTitleRow">
            <span id="ov-brief">cermat. brief</span>
            <span>{PERIOD_LABEL[period]}</span>
          </div>
          {briefLoading && !brief ? (
            <p className="overviewLoading">Summarising metrics…</p>
          ) : brief ? (
            <>
              <div className="briefLines">
                {brief.lines.map((line, index) => (
                  <p key={index}>{line}</p>
                ))}
              </div>
              <p className="briefMode">
                {brief.mode === "model"
                  ? "Wording by model · every figure from computed metrics"
                  : "Written directly from computed metrics"}
              </p>
              <button
                type="button"
                className="briefAsk"
                onClick={() => onAsk(brief.suggested_question ?? "What changed in the last 30 days?")}
              >
                Ask cermat. about this →
              </button>
            </>
          ) : (
            <p className="overviewEmpty">Brief unavailable right now.</p>
          )}
        </section>

        <section className="ovSuppliers" aria-labelledby="ov-suppliers">
          <div className="sectionTitleRow">
            <span id="ov-suppliers">Supplier signals</span>
            <span>open exceptions</span>
          </div>
          {overview.suppliers.length === 0 ? (
            <p className="overviewEmpty">No suppliers extracted yet.</p>
          ) : (
            (() => {
              const max = Math.max(1, ...overview.suppliers.map((s) => s.open_issue_count));
              return overview.suppliers.map((supplier) => (
                <button
                  type="button"
                  key={supplier.supplier_key}
                  className="barRow"
                  onClick={() => onSupplier(supplier.supplier_key)}
                >
                  <span className="barLabel">
                    {supplier.supplier_name}
                    <small>
                      {supplier.transaction_count} txn
                      {supplier.issue_rate !== null && ` · ${supplier.issue_rate}% with exceptions`}
                    </small>
                  </span>
                  <span className="barTrack" aria-hidden="true">
                    <i style={{ width: `${(supplier.open_issue_count / max) * 100}%` }} />
                  </span>
                  <span className="barValue">{supplier.open_issue_count}</span>
                </button>
              ));
            })()
          )}
        </section>

        <section className="ovTrend" aria-labelledby="ov-trend">
          <div className="sectionTitleRow">
            <span id="ov-trend">Exception trend · {PERIOD_LABEL[period]}</span>
            <span>
              {trend.direction ? DIRECTION_LABEL[trend.direction] : "—"}
            </span>
          </div>
          <p className="trendSummary">
            <strong>{trend.total_created}</strong> created ·{" "}
            <strong>{trend.total_resolved}</strong> resolved
            {trend.previous_period_created !== null &&
              ` · ${trend.previous_period_created} created in the previous ${PERIOD_LABEL[period]}`}
          </p>
          {trend.sufficient_history ? (
            <TrendChart trend={trend} />
          ) : (
            <p className="overviewEmpty">{trend.message}</p>
          )}
        </section>

        <section className="ovMix" aria-labelledby="ov-mix">
          <div className="sectionTitleRow">
            <span id="ov-mix">Issue mix</span>
            <span>open</span>
          </div>
          {overview.issue_mix.length === 0 ? (
            <p className="overviewEmpty">No open exceptions.</p>
          ) : (
            overview.issue_mix.map((row) => (
              <div className="barRow static" key={row.family}>
                <span className="barLabel">{FAMILY_LABEL[row.family] ?? row.label}</span>
                <span className="barTrack" aria-hidden="true">
                  <i style={{ width: `${row.share}%` }} />
                </span>
                <span className="barValue">
                  {row.count}
                  <small> · {Math.round(row.share)}%</small>
                </span>
              </div>
            ))
          )}
        </section>

        <section className="ovSignals" aria-labelledby="ov-signals">
          <div className="sectionTitleRow">
            <span id="ov-signals">Recurring patterns</span>
            <span>{overview.patterns.length}</span>
          </div>
          {overview.patterns.length === 0 ? (
            <p className="overviewEmpty">
              No recurring patterns yet. A pattern needs the same kind of exception
              repeating across several transactions.
            </p>
          ) : (
            overview.patterns.map((pattern) => (
              <div className="signalRow" key={pattern.key}>
                <span className={`severity ${pattern.severity}`}>{pattern.severity}</span>
                <div>
                  <p>{pattern.title}</p>
                  <small>
                    {pattern.count} of threshold {pattern.threshold} · {dateLabel(pattern.first_seen)} –{" "}
                    {dateLabel(pattern.last_seen)}
                  </small>
                  <span className="signalLinks">
                    {pattern.supplier_key && (
                      <button type="button" className="askTextButton" onClick={() => onSupplier(pattern.supplier_key)}>
                        Supplier
                      </button>
                    )}
                    {pattern.related_transactions.slice(0, 3).map((t) => (
                      <button type="button" key={t.id} className="askTextButton" onClick={() => onOpenTransaction(t.id)}>
                        {t.name}
                      </button>
                    ))}
                  </span>
                </div>
              </div>
            ))
          )}

          <div className="sectionTitleRow signalsSecond">
            <span>Anomaly signals</span>
            <span>{overview.anomalies.length}</span>
          </div>
          {overview.anomalies.length === 0 ? (
            <p className="overviewEmpty">Nothing exceeds the configured thresholds.</p>
          ) : (
            overview.anomalies.map((signal) => (
              <AnomalyRow key={signal.key} signal={signal} onOpenTransaction={onOpenTransaction} />
            ))
          )}
        </section>

        <section className="ovPerformance" aria-labelledby="ov-performance">
          <div className="sectionTitleRow">
            <span id="ov-performance">Review workflow</span>
            <span>{PERIOD_LABEL[period]}</span>
          </div>
          <dl className="compactStats">
            <div>
              <dt>Created / resolved</dt>
              <dd>
                {overview.resolution.issues_created} / {overview.resolution.issues_resolved}
              </dd>
            </div>
            <div>
              <dt>Resolution rate</dt>
              <dd>{percent(overview.resolution.resolution_rate)}</dd>
            </div>
            <div>
              <dt>Median time to resolve</dt>
              <dd>{duration(overview.resolution.median_resolution_hours)}</dd>
            </div>
            <div>
              <dt>Oldest open</dt>
              <dd>
                {overview.resolution.oldest_open_issue_age_days === null
                  ? "—"
                  : `${overview.resolution.oldest_open_issue_age_days.toFixed(1)} d`}
              </dd>
            </div>
          </dl>
        </section>

        <section className="ovQuality" aria-labelledby="ov-quality">
          <div className="sectionTitleRow">
            <span id="ov-quality">Data quality</span>
            <span>
              {overview.data_quality.affected_document_count}/{overview.data_quality.documents_checked} docs
            </span>
          </div>
          <p className="overviewFootnote">
            Extraction completeness — separate from reconciliation exceptions.
          </p>
          {overview.data_quality.checks.length === 0 && overview.data_quality.unmatched_line_count === 0 ? (
            <p className="overviewEmpty">No extraction gaps detected.</p>
          ) : (
            <ul className="qualityList">
              {overview.data_quality.checks.map((check) => (
                <li key={check.check}>
                  <span>{check.label}</span>
                  <strong>{check.count}</strong>
                </li>
              ))}
              {overview.data_quality.unmatched_line_count > 0 && (
                <li>
                  <span>Unmatched line items</span>
                  <strong>{overview.data_quality.unmatched_line_count}</strong>
                </li>
              )}
            </ul>
          )}
        </section>
      </div>
    </section>
  );
}

function PriorityRow({
  item,
  onOpenTransaction,
  onSupplier,
}: {
  item: PriorityItem;
  onOpenTransaction: (id: string) => void;
  onSupplier: (key: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <article className={`priorityRow ${item.priority_band}`}>
      <button
        type="button"
        className="priorityMain"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={`priorityBand ${item.priority_band}`}>{BAND_LABEL[item.priority_band]}</span>
        <span className="priorityText">
          <strong>{item.transaction_name}</strong>
          <span>
            {item.title}
            {item.item_description ? ` — ${item.item_description}` : ""}
          </span>
        </span>
        <span className="priorityFigure">
          {item.variance ? money(item.variance.currency, item.variance.signed_variance, true) : "—"}
          <small>score {item.priority_score}</small>
        </span>
      </button>
      {open && (
        <div className="priorityReasons">
          <ul>
            {item.priority_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <span className="signalLinks">
            <button type="button" className="askTextButton" onClick={() => onOpenTransaction(item.transaction_id)}>
              Open {item.transaction_name}
            </button>
            {item.supplier_key && (
              <button type="button" className="askTextButton" onClick={() => onSupplier(item.supplier_key)}>
                {item.supplier}
              </button>
            )}
          </span>
        </div>
      )}
    </article>
  );
}

function AnomalyRow({
  signal,
  onOpenTransaction,
}: {
  signal: AnomalySignal;
  onOpenTransaction: (id: string) => void;
}) {
  const thresholdText =
    signal.unit === "percent" ? percent(signal.threshold) : observed(signal, signal.threshold);
  const baselineText =
    signal.baseline === null
      ? "—"
      : signal.unit === "percent"
        ? percent(signal.baseline)
        : observed(signal, signal.baseline);
  return (
    <div className="signalRow anomaly">
      <span className={`severity ${signal.severity}`}>{signal.severity}</span>
      <div>
        <p>{signal.title}</p>
        <dl className="signalFigures">
          <div>
            <dt>Observed</dt>
            <dd>{observed(signal, signal.observed_value)}</dd>
          </div>
          <div>
            <dt title={signal.baseline_label}>Baseline</dt>
            <dd>{baselineText}</dd>
          </div>
          <div>
            <dt>Threshold</dt>
            <dd>{thresholdText}</dd>
          </div>
        </dl>
        <small>{signal.reason}</small>
        <span className="signalLinks">
          {signal.related_transactions.slice(0, 3).map((t) => (
            <button type="button" key={t.id} className="askTextButton" onClick={() => onOpenTransaction(t.id)}>
              {t.name}
            </button>
          ))}
        </span>
      </div>
    </div>
  );
}

function ThresholdSettings({ onSaved }: { onSaved: () => void }) {
  const resource = useResource<IntelligenceSettings>("settings", (signal) =>
    api<IntelligenceSettings>("/api/v1/settings/intelligence", { signal })
  );
  const [draft, setDraft] = useState<IntelligenceSettings | null>(null);
  const values = draft ?? resource.data;
  const setValues = setDraft;
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const error = saveError || resource.error;

  async function save(event: FormEvent, reset = false) {
    event.preventDefault();
    if (!values) return;
    setSaving(true);
    setSaveError("");
    try {
      await api("/api/v1/settings/intelligence", {
        method: "PATCH",
        json: reset
          ? { reset: true }
          : {
              high_value_variance_amount: values.high_value_variance_amount,
              high_variance_percentage: values.high_variance_percentage,
              recurring_issue_min_count: values.recurring_issue_min_count,
              recurring_issue_period_days: values.recurring_issue_period_days,
              overdue_review_days: values.overdue_review_days,
            },
      });
      onSaved();
    } catch (err) {
      setSaveError(errorMessage(err, "Could not save thresholds."));
    } finally {
      setSaving(false);
    }
  }

  const fields: Array<[keyof IntelligenceSettings, string, string]> = [
    ["high_value_variance_amount", "High-value variance", "amount, any currency"],
    ["high_variance_percentage", "High variance %", "billed exceptions"],
    ["recurring_issue_min_count", "Recurring pattern at", "exceptions"],
    ["recurring_issue_period_days", "Pattern window", "days"],
    ["overdue_review_days", "Review target", "days"],
  ];

  return (
    <form className="thresholds" onSubmit={(event) => void save(event)}>
      <div className="sectionTitleRow">
        <span>Thresholds</span>
        <span>{values?.is_default ? "defaults" : "customised"}</span>
      </div>
      {!values ? (
        <p className="overviewLoading">{error || "Loading thresholds…"}</p>
      ) : (
        <>
          <div className="thresholdFields">
            {fields.map(([key, label, hint]) => (
              <label key={key}>
                <span>
                  {label} <small>{hint}</small>
                </span>
                <input
                  type="number"
                  step="any"
                  min={0}
                  value={values[key] as number}
                  onChange={(event) =>
                    setValues({ ...values, [key]: Number(event.target.value) })
                  }
                />
              </label>
            ))}
          </div>
          <div className="thresholdActions">
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save thresholds"}
            </button>
            <button type="button" className="askTextButton" disabled={saving} onClick={(event) => void save(event, true)}>
              Reset to defaults
            </button>
          </div>
          {error && <p className="error">{error}</p>}
        </>
      )}
    </form>
  );
}

function SupplierView({
  supplierKey,
  onBack,
  onOpenTransaction,
  onAsk,
}: {
  supplierKey: string;
  onBack: () => void;
  onOpenTransaction: (id: string) => void;
  onAsk: (question: string) => void;
}) {
  const resource = useResource<SupplierDetail>(supplierKey, (signal) =>
    api<SupplierDetail>(`/api/v1/intelligence/suppliers/${encodeURIComponent(supplierKey)}`, { signal })
  );
  const detail = resource.data && resource.data.supplier.supplier_key === supplierKey ? resource.data : null;
  const error = resource.error;

  return (
    <section className="panel overviewPanel supplierPanel">
      <button type="button" className="askTextButton backLink" onClick={onBack}>
        ← Overview
      </button>
      {error ? (
        <div className="askError" role="alert">
          <span>Supplier unavailable</span>
          {error}
        </div>
      ) : !detail ? (
        <p className="overviewLoading">Checking workspace…</p>
      ) : (
        <SupplierBody detail={detail} onOpenTransaction={onOpenTransaction} onAsk={onAsk} />
      )}
    </section>
  );
}

function SupplierBody({
  detail,
  onOpenTransaction,
  onAsk,
}: {
  detail: SupplierDetail;
  onOpenTransaction: (id: string) => void;
  onAsk: (question: string) => void;
}) {
  const s = detail.supplier;
  const openFamilies = Object.entries(detail.open_by_family).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <header className="supplierHeader">
        <div className="panelLabel">Supplier intelligence</div>
        <h2>{s.supplier_name}</h2>
        {s.name_variants.length > 1 && (
          <p className="overviewFootnote">Also extracted as: {s.name_variants.filter((n) => n !== s.supplier_name).join(" · ")}</p>
        )}
      </header>

      <dl className="supplierFigures">
        <div>
          <dt>Transactions</dt>
          <dd>{s.transaction_count}</dd>
        </div>
        <div>
          <dt>Unresolved exceptions</dt>
          <dd>{s.open_issue_count}</dd>
        </div>
        <div>
          <dt>Issue rate</dt>
          <dd>{s.issue_rate === null ? "—" : `${s.issue_rate}%`}</dd>
        </div>
        <div>
          <dt>Avg. time to resolve</dt>
          <dd>{duration(s.average_resolution_time_hours)}</dd>
        </div>
      </dl>
      <p className="overviewFootnote">
        Issue rate = reconciled transactions with at least one exception ÷ reconciled
        transactions ({s.transactions_with_issues} of {s.reconciled_transaction_count}).
      </p>

      <div className="supplierColumns">
        <section>
          <div className="sectionTitleRow">
            <span>Current signals</span>
            <span>open</span>
          </div>
          {openFamilies.length === 0 && detail.missing_document_count === 0 ? (
            <p className="overviewEmpty">No open exceptions.</p>
          ) : (
            <ul className="qualityList">
              {openFamilies.map(([family, count]) => (
                <li key={family}>
                  <span>{FAMILY_LABEL[family] ?? family}</span>
                  <strong>{count}</strong>
                </li>
              ))}
              {detail.missing_document_count > 0 && (
                <li>
                  <span>Transactions missing a document</span>
                  <strong>{detail.missing_document_count}</strong>
                </li>
              )}
            </ul>
          )}
        </section>
        <section>
          <div className="sectionTitleRow">
            <span>Variance</span>
            <span>open, billed</span>
          </div>
          <ul className="qualityList">
            {s.total_variance_amount.length === 0 ? (
              <li>
                <span>Total active variance</span>
                <strong>—</strong>
              </li>
            ) : (
              s.total_variance_amount.map((total) => (
                <li key={total.currency ?? "none"}>
                  <span>Total active variance</span>
                  <strong>{money(total.currency, total.absolute_total)}</strong>
                </li>
              ))
            )}
            <li>
              <span>Average price variance</span>
              <strong>{percent(s.average_variance_percentage)}</strong>
            </li>
          </ul>
        </section>
      </div>

      <section className="supplierSection">
        <div className="sectionTitleRow">
          <span>Pattern</span>
          <span>{detail.patterns.length} recurring</span>
        </div>
        {detail.pattern_summary ? (
          <p className="patternSentence">{detail.pattern_summary.sentence}</p>
        ) : (
          <p className="overviewEmpty">
            Not enough exception history yet. Patterns appear after at least three
            exceptions for this supplier.
          </p>
        )}
        {detail.patterns.map((pattern) => (
          <div className="signalRow" key={pattern.key}>
            <span className={`severity ${pattern.severity}`}>{pattern.severity}</span>
            <div>
              <p>{pattern.title}</p>
              <small>{pattern.count} of threshold {pattern.threshold}</small>
            </div>
          </div>
        ))}
        {detail.anomalies.map((signal) => (
          <AnomalyRow key={signal.key} signal={signal} onOpenTransaction={onOpenTransaction} />
        ))}
      </section>

      <section className="supplierSection">
        <div className="sectionTitleRow">
          <span>Recent exceptions</span>
          <span>{detail.recent_exceptions.length}</span>
        </div>
        {detail.recent_exceptions.length === 0 ? (
          <p className="overviewEmpty">No exceptions recorded for this supplier.</p>
        ) : (
          detail.recent_exceptions.map((row) => (
            <button
              type="button"
              className="exceptionRow"
              key={row.issue_id}
              onClick={() => onOpenTransaction(row.transaction_id)}
            >
              <span className="exceptionName">
                <strong>{row.transaction_name}</strong>
                <small>{dateLabel(row.created_at)}</small>
              </span>
              <span className="exceptionFigure">
                {row.variance ? money(row.variance.currency, row.variance.signed_variance, true) : "—"}
              </span>
              <span className="exceptionTitle">
                {FAMILY_LABEL[row.family] ?? row.family}
                <small>{row.item_description ?? row.title}</small>
              </span>
              <span className={`issueState ${row.status}`}>{row.status}</span>
            </button>
          ))
        )}
      </section>

      <section className="supplierSection">
        <div className="sectionTitleRow">
          <span>Transactions</span>
          <span>{detail.transactions.length}</span>
        </div>
        {detail.transactions.map((t) => (
          <button type="button" className="exceptionRow" key={t.id} onClick={() => onOpenTransaction(t.id)}>
            <span className="exceptionName">
              <strong>{t.name}</strong>
              <small>{dateLabel(t.created_at)}</small>
            </span>
            <span className="exceptionFigure">{t.total === null ? "—" : money(t.currency, t.total)}</span>
            <span className="exceptionTitle">
              {t.open_issue_count > 0 ? `${t.open_issue_count} open` : "No open exceptions"}
              {t.missing_document_types.length > 0 && (
                <small>Missing {t.missing_document_types.map((d) => d.replace("_", " ")).join(", ")}</small>
              )}
            </span>
            <span className={`workflowState ${statusClass(t.status)}`}>{statusLabel(t.status)}</span>
          </button>
        ))}
      </section>

      <button
        type="button"
        className="briefAsk"
        onClick={() => onAsk(`Why is ${s.supplier_name.replace(/\.$/, "")} appearing in the priority queue?`)}
      >
        Ask cermat. about this supplier →
      </button>
    </>
  );
}
