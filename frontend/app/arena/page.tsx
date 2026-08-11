"use client";

// Model A versus Model B on an identical scenario, seed and simulator config
// (spec section 60). The runs happen back to back on the one CARLA server, so
// the only difference between them is the model.

import { useCallback, useEffect, useState } from "react";
import { API_BASE, api, type Model, type Scenario } from "../../lib/api";

type Side = {
  experiment_id: string;
  model_id: string;
  status: string;
  scenario_id: string;
  seed: number;
  result: string | null;
  score: number | null;
  collisions: number | null;
  minimum_ttc: number | null;
  lane_invasions: number | null;
  route_completion: number | null;
  average_speed: number | null;
  distance_m: number | null;
  latency_p50: number | null;
  latency_p95: number | null;
  termination_reason: string | null;
};

type Comparison = { fair: boolean; detail: string; a: Side; b: Side };

const DONE = ["COMPLETED", "FAILED", "STOPPED"];

/** Rows of the comparison table: label, accessor, formatter, and who wins. */
const ROWS: {
  label: string;
  get: (s: Side) => number | string | null;
  format: (v: number | string | null) => string;
  better?: "lower" | "higher";
}[] = [
  { label: "RESULT", get: (s) => s.result, format: (v) => (v as string) ?? "-" },
  { label: "SCORE", get: (s) => s.score, format: (v) => (v === null ? "-" : (v as number).toFixed(1)), better: "higher" },
  { label: "COLLISIONS", get: (s) => s.collisions, format: (v) => (v === null ? "-" : String(v)), better: "lower" },
  { label: "MIN TTC (s)", get: (s) => s.minimum_ttc, format: (v) => (v === null ? "n/a" : (v as number).toFixed(2)), better: "higher" },
  { label: "LANE INVASIONS", get: (s) => s.lane_invasions, format: (v) => (v === null ? "-" : String(v)), better: "lower" },
  { label: "ROUTE (%)", get: (s) => s.route_completion, format: (v) => (v === null ? "-" : (v as number).toFixed(1)), better: "higher" },
  { label: "DISTANCE (m)", get: (s) => s.distance_m, format: (v) => (v === null ? "-" : (v as number).toFixed(1)), better: "higher" },
  { label: "AVG SPEED (m/s)", get: (s) => s.average_speed, format: (v) => (v === null ? "-" : (v as number).toFixed(1)) },
  { label: "LATENCY p50 (ms)", get: (s) => s.latency_p50, format: (v) => (v === null ? "-" : (v as number).toFixed(2)), better: "lower" },
  { label: "LATENCY p95 (ms)", get: (s) => s.latency_p95, format: (v) => (v === null ? "-" : (v as number).toFixed(2)), better: "lower" },
  { label: "ENDED BY", get: (s) => s.termination_reason, format: (v) => (v as string) || "-" },
];

export default function Arena() {
  const [models, setModels] = useState<Model[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [modelA, setModelA] = useState("");
  const [modelB, setModelB] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const [seed, setSeed] = useState(42);

  const [pair, setPair] = useState<{ a: string; b: string } | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.models().then((rows) => {
      setModels(rows);
      if (rows[0]) setModelA(rows[0].id);
      if (rows[1]) setModelB(rows[1].id);
    }).catch((e) => setError(String(e)));
    api.scenarios().then((rows) => {
      setScenarios(rows);
      if (rows[0]) setScenarioId(rows[0].id);
    }).catch((e) => setError(String(e)));
  }, []);

  const refresh = useCallback(async (a: string, b: string) => {
    const response = await fetch(`${API_BASE}/api/compare?a=${a}&b=${b}`);
    if (response.ok) setComparison(await response.json());
  }, []);

  useEffect(() => {
    if (!pair) return;
    const timer = setInterval(() => {
      refresh(pair.a, pair.b).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [pair, refresh]);

  const run = async () => {
    setBusy(true);
    setError(null);
    setComparison(null);
    try {
      const response = await fetch(`${API_BASE}/api/arena`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_a: modelA, model_b: modelB,
          scenario_id: scenarioId, seed,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const created = await response.json();
      setPair({ a: created.experiment_a, b: created.experiment_b });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const running =
    comparison !== null &&
    (!DONE.includes(comparison.a.status) || !DONE.includes(comparison.b.status));

  const winnerClass = (row: (typeof ROWS)[number], mine: Side, theirs: Side) => {
    if (!row.better) return "";
    const x = row.get(mine), y = row.get(theirs);
    if (typeof x !== "number" || typeof y !== "number" || x === y) return "";
    const iWin = row.better === "higher" ? x > y : x < y;
    return iWin ? "good" : "";
  };

  return (
    <div className="app">
      <div className="topbar">
        <div className="title">MODEL ARENA</div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {comparison && !comparison.fair && (
            <span className="pill bad">{comparison.detail}</span>
          )}
          {running && <span className="pill good">RUNNING</span>}
          <a href="/" className="pill" style={{ textDecoration: "none" }}>LIVE</a>
        </div>
      </div>

      <div className="main" style={{ gridTemplateColumns: "260px 1fr" }}>
        <div className="panel">
          <div className="label">MATCH</div>

          <div className="field">
            <div className="label">MODEL A</div>
            <select value={modelA} onChange={(e) => setModelA(e.target.value)}
                    disabled={running}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </div>

          <div className="field">
            <div className="label">MODEL B</div>
            <select value={modelB} onChange={(e) => setModelB(e.target.value)}
                    disabled={running}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </div>

          <div className="field">
            <div className="label">SCENARIO</div>
            <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}
                    disabled={running}>
              {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>

          <div className="field">
            <div className="label">SEED</div>
            <input type="number" value={seed} disabled={running}
                   onChange={(e) => setSeed(Number(e.target.value))} />
          </div>

          <button onClick={run}
                  disabled={busy || running || !modelA || !modelB || modelA === modelB}
                  style={{ marginTop: 14 }}>
            RUN BOTH
          </button>
          {modelA === modelB && (
            <div className="warn" style={{ fontSize: 11, marginTop: 10 }}>
              pick two different models
            </div>
          )}
          <div className="muted" style={{ fontSize: 11, marginTop: 14, lineHeight: 1.7 }}>
            Both runs use the same scenario, seed and simulator config, back to
            back on the one CARLA server. The only difference is the model.
          </div>
          {error && <div className="bad" style={{ marginTop: 12, fontSize: 11 }}>{error}</div>}
        </div>

        <div className="panel">
          {!comparison && (
            <div className="placeholder">
              no match yet - pick two models and run
            </div>
          )}
          {comparison && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "10px 8px" }} />
                  {[comparison.a, comparison.b].map((side, i) => (
                    <th key={i} style={{ textAlign: "left", padding: "10px 8px" }}>
                      <div>{side.model_id}</div>
                      <div className="muted" style={{ fontSize: 10 }}>
                        {side.experiment_id} · {side.status}
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROWS.map((row) => (
                  <tr key={row.label} style={{ borderTop: "1px solid var(--line)" }}>
                    <td className="muted" style={{ padding: "9px 8px", fontSize: 11 }}>
                      {row.label}
                    </td>
                    <td className={winnerClass(row, comparison.a, comparison.b)}
                        style={{ padding: "9px 8px" }}>
                      {row.format(row.get(comparison.a))}
                    </td>
                    <td className={winnerClass(row, comparison.b, comparison.a)}
                        style={{ padding: "9px 8px" }}>
                      {row.format(row.get(comparison.b))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="statusbar" style={{ gridTemplateColumns: "1fr 1fr" }}>
        {[comparison?.a, comparison?.b].map((side, i) => (
          <div className="stat" key={i}>
            <div className="k">{side?.model_id ?? (i === 0 ? "A" : "B")}</div>
            <div className={`v ${side?.result === "PASS" ? "good" : side?.result === "FAIL" ? "bad" : ""}`}>
              {side?.result ?? side?.status ?? "-"}
              {side?.score !== null && side?.score !== undefined
                ? `  ${side.score.toFixed(0)}` : ""}
            </div>
          </div>
        ))}
      </div>

      <div className="timeline">
        <div className="label">REPLAYS</div>
        {comparison ? (
          <div style={{ display: "flex", gap: 18 }}>
            {[comparison.a, comparison.b].map((side) => (
              <a key={side.experiment_id} href={`/replay/${side.experiment_id}`}
                 style={{ color: "var(--accent)", fontSize: 12 }}>
                {side.model_id} -&gt; {side.experiment_id}
              </a>
            ))}
          </div>
        ) : (
          <div className="placeholder">run a match first</div>
        )}
      </div>
    </div>
  );
}
