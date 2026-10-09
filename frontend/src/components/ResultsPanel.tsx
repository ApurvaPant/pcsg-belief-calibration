import { useEffect, useState, type ReactNode } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { fetchDashboard, fetchResultsRuns, type ResultRun } from "../api";

// ONE fixed colour per system / signal across every chart (mirrors backend schema).
const SYS: Record<string, string> = {
  pcsg: "#5b6cff", no_memory: "#9aa6b8", vector_rag: "#38c7e8", full_context: "#f2c558", mem0: "#b197fc",
};
const SIG: Record<string, string> = {
  verbalized: "#5b6cff", self_consistency: "#5cd39a", evidence: "#fb8a8a",
};
const RECALS = ["raw", "temperature", "platt", "isotonic"];

function Watermark() {
  return <div className="rv-wm">DRY RUN — SYNTHETIC</div>;
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="rv-section">
      <div className="rv-shead"><h3>{title}</h3>{subtitle && <span>{subtitle}</span>}</div>
      {children}
    </section>
  );
}

export default function ResultsPanel({ onClose }: { onClose: () => void }) {
  const [runs, setRuns] = useState<ResultRun[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [dash, setDash] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchResultsRuns().then((r) => { setRuns(r); setRunId(r[0]?.run_id ?? null); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(() => {
    if (!runId) return;
    setDash(null);
    fetchDashboard(runId).then(setDash).catch((e) => setErr(String(e)));
  }, [runId]);

  const dry = dash?.watermark != null;

  if (err) return <div className="results"><div className="rv-head"><h1>Results</h1><button className="mem-close" onClick={onClose}>✕</button></div><p className="rv-empty">{err}</p></div>;
  return (
    <div className="results">
      <div className="rv-head">
        <div>
          <h1>Experiment results</h1>
          <p className="rv-sub">Read-only — from <code>results/</code>, no model calls.</p>
        </div>
        <div className="rv-pick">
          <select value={runId ?? ""} onChange={(e) => setRunId(e.target.value)}>
            {runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id} · {r.mode}</option>)}
          </select>
          <button className="mem-close" onClick={onClose} aria-label="Back">✕</button>
        </div>
      </div>

      {dry && <div className="rv-banner">DRY RUN — SYNTHETIC DATA, NOT RESULTS</div>}
      {!dash && <p className="rv-empty">Loading…</p>}
      {dash && (
        <div className="rv-body">
          <MetaBar dash={dash} />
          <KpiStrip dash={dash} dry={dry} />
          <Baselines dash={dash} dry={dry} />
          <TokenGrowth dash={dash} dry={dry} />
          <Frontier dash={dash} dry={dry} />
          <Calibration dash={dash} dry={dry} />
          <Abstention dash={dash} dry={dry} />
          <GateFunnel dash={dash} dry={dry} />
          <PersonaReplay dash={dash} dry={dry} />
        </div>
      )}
    </div>
  );
}

function MetaBar({ dash }: any) {
  const m = dash.meta;
  return (
    <div className="rv-meta">
      <span>run <b>{m.run_id}</b></span><span>mode <b>{m.mode}</b></span>
      <span>sha <code>{(m.git_sha || "").slice(0, 7)}</code></span>
      <span>personas <b>{m.sample_sizes?.personas ?? "—"}</b></span>
      <span>test <b>{m.sample_sizes?.test_personas ?? "—"}</b></span>
      <span>assertions <b>{m.sample_sizes?.labelled_assertions ?? "—"}</b></span>
      <span>failed-extractions <b>{m.sample_sizes?.failed_extractions ?? 0}</b></span>
      <span>tokenizer <b>{m.tokenizer}</b></span>
    </div>
  );
}

function kpiCard(label: string, value: string, sub?: string) {
  return <div className="rv-kpi"><div className="rv-kpi-v">{value}</div><div className="rv-kpi-l">{label}</div>{sub && <div className="rv-kpi-s">{sub}</div>}</div>;
}

// A. KPI strip
function KpiStrip({ dash }: any) {
  const k = dash.kpi || {};
  return (
    <Section title="A · Headline" subtitle="every number shows CI + n once live">
      <div className="rv-kpis">
        {kpiCard("tokens/Q · PCSG vs full-context",
          k.tokens_per_q_pcsg != null ? `${k.tokens_per_q_pcsg} vs ${k.tokens_per_q_full}` : "—",
          k.pct_saved != null ? `${k.pct_saved}% saved` : undefined)}
        {kpiCard("ECE before → after recalibration",
          k.ece_before != null ? `${k.ece_before} → ${k.ece_after}` : "—", "verbalized signal")}
        {kpiCard("current-state accuracy (PCSG)",
          k.baselines?.pcsg?.mean != null ? `${(k.baselines.pcsg.mean * 100).toFixed(0)}%` : "— (live)")}
        {kpiCard("fabrication rate", k.fabrication_rate?.mean != null ? `${(k.fabrication_rate.mean * 100).toFixed(0)}%` : "— (live)")}
      </div>
    </Section>
  );
}

// B. Baselines scatter + table
function Baselines({ dash, dry }: any) {
  const data = (dash.baselines || []).map((b: any) => ({
    system: b.system, x: b.tokens_per_q ?? 0, y: (b.accuracy?.mean ?? b.accuracy ?? 0),
    color: b.color || SYS[b.system] || "#888",
  }));
  return (
    <Section title="B · Does the knowledge graph help?" subtitle="accuracy vs tokens / question">
      {data.length === 0 ? <p className="rv-note">No baseline data yet — populated by STEP 8 on the live run.</p> : (
        <div className="rv-chart">
          {dry && <Watermark />}
          <ResponsiveContainer width="100%" height={260}>
            <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
              <CartesianGrid strokeOpacity={0.15} />
              <XAxis type="number" dataKey="x" name="tokens/Q" label={{ value: "tokens / question", position: "bottom" }} />
              <YAxis type="number" dataKey="y" name="accuracy" domain={[0, 1]} label={{ value: "accuracy", angle: -90, position: "insideLeft" }} />
              <Tooltip />
              <Scatter data={data}>{data.map((d: any, i: number) => <Cell key={i} fill={d.color} />)}</Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </Section>
  );
}

// C. Token growth
function TokenGrowth({ dash, dry }: any) {
  const data = (dash.token_growth || []).map((p: any) => ({
    turn: p.turn, raw: p.raw?.mean ?? p.raw, pcsg: p.pcsg?.mean ?? p.pcsg,
    rawBand: [p.raw?.iqr?.[0] ?? 0, p.raw?.iqr?.[1] ?? 0], pcsgBand: [p.pcsg?.iqr?.[0] ?? 0, p.pcsg?.iqr?.[1] ?? 0],
  }));
  return (
    <Section title="C · Cost as conversations grow" subtitle="tokens injected per answer vs turn number">
      <div className="rv-chart">
        {dry && <Watermark />}
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={data} margin={{ top: 10, right: 20, bottom: 25, left: 10 }}>
            <CartesianGrid strokeOpacity={0.15} />
            <XAxis dataKey="turn" label={{ value: "turn", position: "bottom" }} />
            <YAxis label={{ value: "tokens", angle: -90, position: "insideLeft" }} />
            <Tooltip /><Legend />
            <Area dataKey="rawBand" stroke="none" fill={SYS.full_context} fillOpacity={0.12} name="full-context IQR" />
            <Area dataKey="pcsgBand" stroke="none" fill={SYS.pcsg} fillOpacity={0.12} name="PCSG IQR" />
            <Line dataKey="raw" stroke={SYS.full_context} strokeWidth={2} name="full-context history" dot={false} />
            <Line dataKey="pcsg" stroke={SYS.pcsg} strokeWidth={2} name="PCSG state block" dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Section>
  );
}

// D. Cost–accuracy frontier
function Frontier({ dash, dry }: any) {
  const cells = dash.cost_curve?.cells || [];
  const [tier1, setTier1] = useState(0);
  const [wtau, setWtau] = useState<string>("off");
  const tiers = [...new Set(cells.map((c: any) => c.tier1))].sort((a: any, b: any) => a - b);
  const data = cells.map((c: any) => ({ ...c, x: c.cost_tokens, y: c.accuracy }));
  const sel = cells.find((c: any) => c.tier1 === tier1 && c.write_tau === wtau);
  return (
    <Section title="D · Cost–accuracy frontier" subtitle="each replay grid cell; Pareto marked">
      {cells.length === 0 ? <p className="rv-note">No cost-curve cells.</p> : (
        <>
          <div className="rv-controls">
            <label>tier-1 threshold
              <input type="range" min={0} max={Math.max(...(tiers as number[]))} step={1}
                value={tier1} onChange={(e) => setTier1(Number(e.target.value))}
                list="tiers" /></label>
            <span>write_τ {["off", "0.6", "0.8"].map((w) => (
              <button key={w} className={`rv-tog ${wtau === w ? "on" : ""}`} onClick={() => setWtau(w)}>{w}</button>))}</span>
            {sel && <span className="rv-selnum">cell {sel.cell}: acc {sel.accuracy}, cost {sel.cost_tokens}{sel.pareto ? " · Pareto" : ""}</span>}
          </div>
          <div className="rv-chart">
            {dry && <Watermark />}
            <ResponsiveContainer width="100%" height={260}>
              <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
                <CartesianGrid strokeOpacity={0.15} />
                <XAxis type="number" dataKey="x" name="cost (tokens)" label={{ value: "cost (tokens)", position: "bottom" }} />
                <YAxis type="number" dataKey="y" name="accuracy" domain={[0, 1]} label={{ value: "current-state accuracy", angle: -90, position: "insideLeft" }} />
                <Tooltip />
                <Scatter data={data}>{data.map((c: any, i: number) => (
                  <Cell key={i} fill={c.tier1 === tier1 && c.write_tau === wtau ? "#ff5a5a" : (c.pareto ? SYS.pcsg : "#9aa6b8")} />))}</Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Section>
  );
}

// E. Calibration
function Calibration({ dash, dry }: any) {
  const signals = dash.calibration?.signals || {};
  const sigNames = Object.keys(signals);
  const [sig, setSig] = useState(sigNames[0]);
  const [rec, setRec] = useState("raw");
  const s = signals[sig || sigNames[0]];
  if (!s) return <Section title="E · Calibration"><p className="rv-note">No calibration data.</p></Section>;
  const rel = (rec === "raw" ? s.reliability?.before : s.reliability?.after) || [];
  const diag = rel.map((b: any) => ({ conf: b.conf, acc: b.acc, ideal: b.conf }));
  const m = s.recalibrators?.[rec] || s.recalibrators?.raw || {};
  return (
    <Section title="E · Calibration" subtitle="reliability diagram (perfect = diagonal)">
      <div className="rv-controls">
        <span>signal {sigNames.map((n) => <button key={n} className={`rv-tog ${sig === n ? "on" : ""}`} style={{ borderColor: SIG[n] }} onClick={() => setSig(n)}>{n}</button>)}</span>
        <span>recalibrator {RECALS.map((n) => <button key={n} className={`rv-tog ${rec === n ? "on" : ""}`} onClick={() => setRec(n)}>{n}</button>)}</span>
        <span className="rv-selnum">ECE {m.ece ?? "—"} · Brier {m.brier ?? "—"} · AUROC {m.auroc ?? "—"}</span>
      </div>
      <div className="rv-chart">
        {dry && <Watermark />}
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={diag} margin={{ top: 10, right: 20, bottom: 25, left: 10 }}>
            <CartesianGrid strokeOpacity={0.15} />
            <XAxis dataKey="conf" type="number" domain={[0, 1]} label={{ value: "confidence", position: "bottom" }} />
            <YAxis domain={[0, 1]} label={{ value: "accuracy", angle: -90, position: "insideLeft" }} />
            <Tooltip />
            <Line dataKey="ideal" stroke="#888" strokeDasharray="4 4" dot={false} name="perfect" />
            <Line dataKey="acc" stroke={SIG[sig] || "#5b6cff"} strokeWidth={2} name={sig} />
          </LineChart>
        </ResponsiveContainer>
        <ResponsiveContainer width="100%" height={110}>
          <BarChart data={s.histogram || []} margin={{ top: 4, right: 20, bottom: 20, left: 10 }}>
            <XAxis dataKey="bin" label={{ value: "confidence histogram", position: "bottom" }} />
            <YAxis /><Tooltip />
            <Bar dataKey="count" fill={SIG[sig] || "#5b6cff"} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Section>
  );
}

// F. Abstention
function Abstention({ dash, dry }: any) {
  const sweep = dash.abstention?.sweep || {};
  const variants = Object.keys(sweep);
  const [tau, setTau] = useState(0.5);
  if (variants.length === 0) return <Section title="F · Abstention"><p className="rv-note">No abstention data.</p></Section>;
  const merged = (sweep[variants[0]] || []).map((pt: any, i: number) => {
    const row: any = { tau: pt.tau };
    variants.forEach((v) => { row[v] = sweep[v][i]?.risk ?? (1 - (sweep[v][i]?.accuracy ?? 1)); row[`${v}_cov`] = sweep[v][i]?.coverage; });
    return row;
  });
  const at = (v: string) => (sweep[v] || []).reduce((best: any, p: any) => Math.abs(p.tau - tau) < Math.abs((best?.tau ?? 9) - tau) ? p : best, null);
  return (
    <Section title="F · Abstention" subtitle="risk vs coverage (raw vs calibrated)">
      <div className="rv-controls">
        <label>τ {tau.toFixed(2)}<input type="range" min={0} max={1} step={0.05} value={tau} onChange={(e) => setTau(Number(e.target.value))} /></label>
        {variants.map((v) => { const p = at(v); return p && <span key={v} className="rv-selnum">{v}: cov {p.coverage}, acc {p.accuracy}, fab {p.fabrication}</span>; })}
      </div>
      <div className="rv-chart">
        {dry && <Watermark />}
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={merged} margin={{ top: 10, right: 20, bottom: 25, left: 10 }}>
            <CartesianGrid strokeOpacity={0.15} />
            <XAxis dataKey="tau" label={{ value: "abstain τ", position: "bottom" }} />
            <YAxis label={{ value: "risk (1−acc)", angle: -90, position: "insideLeft" }} />
            <Tooltip /><Legend />
            {variants.map((v, i) => <Line key={v} dataKey={v} stroke={i === 0 ? SYS.pcsg : SYS.vector_rag} dot={false} name={v} />)}
            <ReferenceLine x={tau} stroke="#ff5a5a" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </Section>
  );
}

// G. Gate funnel + token stacking
function GateFunnel({ dash, dry }: any) {
  const stages = dash.gate_funnel?.stages || [];
  return (
    <Section title="G · Where the tokens go" subtitle="gate funnel + tokens per stage">
      <div className="rv-chart">
        {dry && <Watermark />}
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={stages} margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
            <CartesianGrid strokeOpacity={0.15} />
            <XAxis dataKey="stage" label={{ value: "pipeline stage", position: "bottom" }} />
            <YAxis yAxisId="l" label={{ value: "messages", angle: -90, position: "insideLeft" }} />
            <YAxis yAxisId="r" orientation="right" label={{ value: "tokens", angle: 90, position: "insideRight" }} />
            <Tooltip /><Legend />
            <Bar yAxisId="l" dataKey="count" fill={SYS.pcsg} name="messages" />
            <Bar yAxisId="r" dataKey="tokens" fill={SYS.full_context} name="tokens" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Section>
  );
}

// H. Persona replay stepper
function PersonaReplay({ dash, dry }: any) {
  const personas = dash.persona_replay || [];
  const [pi, setPi] = useState(0);
  const [turn, setTurn] = useState(1);
  const p = personas[pi];
  const turns = p?.turns || [];
  const upto = turns.slice(0, turn);
  const line = upto.map((t: any) => ({ turn: t.turn, pcsg: t.cum_tokens_pcsg, full: t.cum_tokens_full }));
  const current = turns[turn - 1];
  if (personas.length === 0) return <Section title="H · Persona replay"><p className="rv-note">No replay data.</p></Section>;
  return (
    <Section title="H · Persona replay" subtitle="step turn by turn; cumulative tokens + new assertions">
      <div className="rv-controls">
        <span>persona {personas.map((pp: any, i: number) => <button key={i} className={`rv-tog ${pi === i ? "on" : ""}`} onClick={() => { setPi(i); setTurn(1); }}>#{pp.persona}</button>)}</span>
        <label>turn {turn}/{turns.length}<input type="range" min={1} max={turns.length} value={turn} onChange={(e) => setTurn(Number(e.target.value))} /></label>
      </div>
      <div className="rv-replay">
        <div className="rv-chart" style={{ flex: 1 }}>
          {dry && <Watermark />}
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={line} margin={{ top: 10, right: 20, bottom: 25, left: 10 }}>
              <CartesianGrid strokeOpacity={0.15} />
              <XAxis dataKey="turn" label={{ value: "turn", position: "bottom" }} />
              <YAxis label={{ value: "cumulative tokens", angle: -90, position: "insideLeft" }} />
              <Tooltip /><Legend />
              <Line dataKey="full" stroke={SYS.full_context} dot={false} name="full-context" />
              <Line dataKey="pcsg" stroke={SYS.pcsg} dot={false} name="PCSG" />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="rv-turn">
          <div className="rv-turn-msg">“{current?.text}”</div>
          {(current?.assertions || []).length === 0 ? <div className="rv-note">no new assertions</div> :
            (current.assertions).map((a: any, i: number) => (
              <div key={i} className={`rv-assert ${a.correct ? "ok" : "bad"}`}>
                <span>{a.dimension}/{a.key} = {a.value}</span>
                <span className="rv-conf">{Math.round((a.confidence ?? 0) * 100)}% {a.correct ? "✓" : "✗"}</span>
              </div>))}
        </div>
      </div>
    </Section>
  );
}
