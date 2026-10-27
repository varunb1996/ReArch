import { apiFetch } from "./api";
import { READINESS_COLOR, READINESS_TEXT, clusterColor } from "./colors";
import Markdown from "./Markdown";

const pct = (x) => (x === null || x === undefined ? "n/a" : `${Math.round(x * 100)}%`);

function Metric({ label, value, hint }) {
  return (
    <div className="mig-metric" title={hint}>
      <span className="mig-metric-value">{value}</span>
      <span className="mig-metric-label">{label}</span>
    </div>
  );
}

function SubsystemCard({ sub, onShow }) {
  return (
    <div className="mig-card" style={{ borderLeftColor: clusterColor(sub.id) }}>
      <div className="mig-card-head">
        <strong>#{sub.id} {sub.label}</strong>
        <span className={`mig-badge ${sub.readiness}`}>{READINESS_TEXT[sub.readiness]}</span>
      </div>

      <div className="mig-metrics">
        <Metric label="files" value={sub.files} />
        <Metric label="lines" value={sub.loc} hint="Distinct source lines covered by this subsystem's functions and classes" />
        <Metric label="cohesion" value={pct(sub.cohesion)} hint="Share of this subsystem's dependencies that stay inside it. Low means a weak boundary." />
        <Metric label="instability" value={sub.instability ?? "n/a"} hint="Outgoing / (incoming + outgoing). 1 = depends on others, nothing depends on it; 0 = everything depends on it." />
        <Metric label="confidence" value={pct(sub.confidence)} hint="Share of project-looking calls the analysis could pin to one target" />
      </div>

      {sub.blockers.map((b) => <div key={b} className="mig-blocker">Blocker: {b}</div>)}
      {sub.risks.map((r) => <div key={r} className="mig-risk">Risk: {r}</div>)}

      {sub.exposed_api.length > 0 && (
        <div className="mig-row">
          <span className="mig-row-label">Interface to introduce</span>
          {sub.exposed_api.slice(0, 6).map((a) => <code key={a.id} title={a.path}>{a.name}</code>)}
        </div>
      )}
      {sub.depends_on.length > 0 && (
        <div className="mig-row">
          <span className="mig-row-label">Depends on</span>
          {sub.depends_on.map((d) => <span key={d.subsystem} className="mig-chip">#{d.subsystem}</span>)}
        </div>
      )}
      {sub.third_party.length > 0 && (
        <div className="mig-row">
          <span className="mig-row-label">Third-party</span>
          {sub.third_party.map((p) => <span key={p.package} className="mig-chip">{p.package} ×{p.uses}</span>)}
        </div>
      )}
      {sub.purpose && (
        <details className="mig-purpose">
          <summary>Inferred purpose</summary>
          <p>{sub.purpose}</p>
        </details>
      )}
      <button className="mig-link" onClick={() => onShow(sub.id)}>Open in the code map</button>
    </div>
  );
}

export default function MigrationPanel({ plan, repoId, username, onShowSubsystem }) {
  const byId = Object.fromEntries(plan.subsystems.map((s) => [s.id, s]));
  const s = plan.summary;

  const download = async () => {
    const res = await apiFetch(`/api/repos/${repoId}/migration.md`, username);
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "migration-plan.md";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="migration-view">
      <div className="mig-summary">
        <div>
          <h2>Migration plan</h2>
          <p className="hint">
            Measured from the code's dependency structure. Waves run in extraction order: a wave only depends on the ones before it.
          </p>
        </div>
        <button className="mig-download" onClick={download}>Download as Markdown</button>
      </div>

      <div className="mig-stats">
        <Metric label="subsystems" value={s.subsystems} />
        <Metric label="waves" value={s.waves} />
        <Metric label="faults (cycles)" value={s.cycle_groups} />
        <Metric label="code covered" value={pct(s.coverage)} hint="Share of functions/classes assigned to a subsystem" />
        <Metric label="edge confidence" value={pct(s.overall_confidence)} hint="Share of project-looking calls resolved to a single target" />
        {Object.entries(s.readiness).map(([k, v]) => (
          <div key={k} className="mig-metric">
            <span className="mig-metric-value" style={{ color: READINESS_COLOR[k] }}>{v}</span>
            <span className="mig-metric-label">{READINESS_TEXT[k] || k}</span>
          </div>
        ))}
      </div>

      {plan.narrative ? (
        <section className="mig-narrative">
          <div className="mig-narrative-head">
            <strong>Written plan</strong>
            <span className="hint">Written by an AI from the measured facts below; names are checked against them.</span>
          </div>
          {plan.narration === "running" && <div className="mig-pending">Subsystem notes are still being written. They appear in the code map's field notes when ready.</div>}
          {plan.narration === "partial" && <div className="mig-pending">Some subsystem notes were skipped because the AI service ran out of time.</div>}
          {plan.narrative_warnings?.length > 0 && (
            <div className="mig-warning">
              Unverified references in this summary: {plan.narrative_warnings.join(", ")}. Treat them as suggestions, not facts.
            </div>
          )}
          <Markdown text={plan.narrative} />
        </section>
      ) : plan.narration === "running" ? (
        <section className="mig-narrative writing" aria-live="polite">
          <div className="mig-narrative-head">
            <strong>Written plan</strong>
            <span className="hint">Being written now</span>
          </div>
          <p>The map and every number on this page are final. The written plan, and a note on what each subsystem is for, are still being written. They usually appear within a minute or two, and this page updates by itself.</p>
        </section>
      ) : (
        <section className="mig-narrative">
          <p className="hint">
            {plan.narration === "quota"
              ? "No written plan today: the free AI allowance for the day is used up. Everything measured is complete below, and written plans return when the allowance resets."
              : plan.narration === "skipped"
                ? "The written plan was skipped because the AI service was busy or out of time. The measured plan below is complete."
                : "No written plan: the metrics below are complete, but no AI key was available to turn them into prose."}
          </p>
        </section>
      )}

      <h3 className="mig-h">Extraction order</h3>
      {plan.waves.map((wave) => (
        <section key={wave.index} className="mig-wave">
          <div className="mig-wave-title">
            Wave {wave.index}
            <span className="hint">{wave.index === 0 ? "depends on nothing else: extract first" : `depends only on waves 0-${wave.index - 1}`}</span>
          </div>
          {wave.units.map((unit) => (
            <div key={unit.subsystems.join("+")} className={unit.cyclic ? "mig-cycle" : ""}>
              {unit.cyclic && (
                <div className="mig-cycle-title">
                  Fault: {unit.subsystems.map((id) => `#${id}`).join(" and ")} depend on each other. Cut the links below with an interface before extracting.
                </div>
              )}
              {unit.cyclic && plan.cycles
                .filter((c) => c.members.join() === unit.subsystems.join())
                .map((cycle) => (
                  <div key={cycle.members.join("+")}>
                    {cycle.cut_candidates.slice(0, 8).map((cut) => (
                      <div key={`${cut.from}-${cut.to}`} className="mig-cut">
                        Cut #{cut.from} → #{cut.to} (weight {cut.weight})
                        {cut.examples[0] && <> e.g. <code>{cut.examples[0].source}</code> → <code>{cut.examples[0].target}</code></>}
                      </div>
                    ))}
                    {cycle.cut_candidates.length > 8 && (
                      <div className="mig-cut">...and {cycle.cut_candidates.length - 8} more, cheapest first (included in the downloaded plan's JSON).</div>
                    )}
                    {cycle.order_after_cuts?.length > 0 && (
                      <div className="mig-order">
                        After the cuts, extract in this order: {cycle.order_after_cuts.map((layer) => layer.map((id) => `#${id}`).join(" + ")).join("  →  ")}
                      </div>
                    )}
                  </div>
                ))}
              <div className="mig-cards">
                {unit.subsystems.map((id) => <SubsystemCard key={id} sub={byId[id]} onShow={onShowSubsystem} />)}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
