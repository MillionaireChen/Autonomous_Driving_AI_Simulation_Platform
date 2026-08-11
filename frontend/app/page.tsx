"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import BirdEyeView from "../components/BirdEyeView";
import {
  api,
  telemetryUrl,
  type Episode,
  type Experiment,
  type Model,
  type Scenario,
  type Tick,
} from "../lib/api";

type TimelineEvent = { time: number; type: string; data: Record<string, unknown> };

const RUNNING_STATES = ["CREATED", "STARTING", "RUNNING"];

function ttcClass(ttc: number | null | undefined): string {
  if (ttc === null || ttc === undefined) return "muted";
  if (ttc < 2) return "bad";
  if (ttc < 3) return "warn";
  return "good";
}

export default function Dashboard() {
  const [models, setModels] = useState<Model[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [modelId, setModelId] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const [seed, setSeed] = useState(42);

  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [tick, setTick] = useState<Tick | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [trail, setTrail] = useState<{ x: number; y: number }[]>([]);
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    api
      .models()
      .then((rows) => {
        setModels(rows);
        if (rows[0]) setModelId(rows[0].id);
      })
      .catch((e) => setError(String(e)));
    api
      .scenarios()
      .then((rows) => {
        setScenarios(rows);
        if (rows[0]) setScenarioId(rows[0].id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  // Poll experiment status while it is alive; the socket carries the data,
  // but status transitions are owned by the backend.
  useEffect(() => {
    if (!experiment || !RUNNING_STATES.includes(experiment.status)) return;
    const timer = setInterval(async () => {
      try {
        const latest = await api.experiment(experiment.id);
        setExperiment(latest);
        if (!RUNNING_STATES.includes(latest.status)) {
          const rows = await api.episodes(latest.id).catch(() => []);
          if (rows[0]) setEpisode(rows[0]);
        }
      } catch {
        /* transient; the next tick will retry */
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [experiment]);

  const connect = useCallback((experimentId: string) => {
    socketRef.current?.close();
    const socket = new WebSocket(telemetryUrl(experimentId));
    socketRef.current = socket;
    socket.onmessage = (raw) => {
      const message: Tick = JSON.parse(raw.data);
      if (message.type === "heartbeat" || message.type === "error") return;
      if (message.type === "end") {
        socket.close();
        return;
      }
      if (message.events?.length) {
        setEvents((previous) => [...previous, ...(message.events ?? [])]);
      }
      if (message.sim_time === undefined) return;
      setTick(message);
      if (message.x !== undefined && message.y !== undefined) {
        setTrail((previous) =>
          [...previous, { x: message.x as number, y: message.y as number }].slice(-400),
        );
      }
    };
  }, []);

  const run = async () => {
    setBusy(true);
    setError(null);
    setEvents([]);
    setTrail([]);
    setTick(null);
    setEpisode(null);
    try {
      const created = await api.createExperiment({
        model_id: modelId,
        scenario_id: scenarioId,
        seed,
      });
      setExperiment(created);
      const started = await api.start(created.id);
      setExperiment(started);
      connect(created.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!experiment) return;
    try {
      setExperiment(await api.stop(experiment.id));
    } catch (e) {
      setError(String(e));
    }
  };

  const live = experiment !== null && RUNNING_STATES.includes(experiment.status);
  const speedKph = ((tick?.speed_mps ?? 0) * 3.6).toFixed(0);
  const ttc = tick?.ttc_s ?? null;

  return (
    <div className="app">
      <div className="topbar">
        <div className="title">AUTONOMOUS DRIVING AI ARENA</div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {experiment && <span className="pill">{experiment.id}</span>}
          <span className={`pill ${live ? "good" : ""}`}>
            {experiment?.status ?? "IDLE"}
          </span>
        </div>
      </div>

      <div className="main">
        {/* ---------------- experiment configuration ---------------- */}
        <div className="panel">
          <div className="label">EXPERIMENT</div>

          <div className="field">
            <div className="label">MODEL</div>
            <select
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              disabled={live}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <div className="label">SCENARIO</div>
            <select
              value={scenarioId}
              onChange={(e) => setScenarioId(e.target.value)}
              disabled={live}
            >
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <div className="label">SEED</div>
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
              disabled={live}
            />
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 18 }}>
            <button onClick={run} disabled={busy || live || !modelId || !scenarioId}>
              START
            </button>
            <button onClick={stop} disabled={!live} className="bad">
              STOP
            </button>
          </div>

          {error && (
            <div className="bad" style={{ marginTop: 14, fontSize: 11 }}>
              {error}
            </div>
          )}

          {episode && (
            <div style={{ marginTop: 22 }}>
              <div className="label">RESULT</div>
              <div
                className={episode.result === "PASS" ? "good" : "bad"}
                style={{ fontSize: 26, margin: "6px 0" }}
              >
                {episode.result}
              </div>
              <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                <div>score {episode.score.toFixed(1)} / 100</div>
                <div>collisions {episode.collision_count}</div>
                <div>lane invasions {episode.lane_invasion_count}</div>
                <div>
                  min TTC{" "}
                  {episode.minimum_ttc === null
                    ? "n/a"
                    : `${episode.minimum_ttc.toFixed(2)} s`}
                </div>
                <div>route {episode.route_completion.toFixed(1)}%</div>
                <div className="muted">{episode.termination_reason}</div>
              </div>
            </div>
          )}
        </div>

        {/* ---------------- camera + bird-eye view ---------------- */}
        <div className="center">
          <div className="view">
            <div className="view-label">FRONT CAMERA</div>
            {tick?.camera_front ? (
              <img
                src={`data:image/jpeg;base64,${tick.camera_front}`}
                alt="front camera"
              />
            ) : (
              <div className="placeholder">no signal</div>
            )}
          </div>
          <div className="view">
            <div className="view-label">BIRD-EYE VIEW</div>
            <BirdEyeView tick={tick} trail={trail} />
          </div>
        </div>

        {/* ---------------- model panel ---------------- */}
        <div className="panel">
          <div className="label">MODEL</div>
          <div style={{ margin: "6px 0 20px" }}>
            {models.find((m) => m.id === (experiment?.model_id ?? modelId))?.name ??
              "-"}
          </div>

          <div className="label">ACTION</div>
          {(
            [
              ["THROTTLE", tick?.throttle ?? 0, "var(--good)", 0, 1],
              ["BRAKE", tick?.brake ?? 0, "var(--bad)", 0, 1],
              ["STEER", tick?.steer ?? 0, "var(--accent)", -1, 1],
            ] as [string, number, string, number, number][]
          ).map(([name, value, colour, min, max]) => (
            <div key={name} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="muted" style={{ fontSize: 11 }}>
                  {name}
                </span>
                <span style={{ fontSize: 12 }}>{value.toFixed(2)}</span>
              </div>
              <div className="bar">
                <div
                  style={{
                    width: `${((value - min) / (max - min)) * 100}%`,
                    background: colour,
                  }}
                />
              </div>
            </div>
          ))}

          <div style={{ marginTop: 22 }}>
            <div className="label">SCENARIO VEHICLE</div>
            <div style={{ fontSize: 12, lineHeight: 1.8 }}>
              <div>
                gap{" "}
                {tick?.npc_gap_m === undefined
                  ? "-"
                  : `${tick.npc_gap_m.toFixed(1)} m`}
              </div>
              <div>
                speed{" "}
                {tick?.npc_speed_mps === undefined
                  ? "-"
                  : `${tick.npc_speed_mps.toFixed(1)} m/s`}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ---------------- status bar ---------------- */}
      <div className="statusbar">
        <div className="stat">
          <div className="k">SPEED</div>
          <div className="v">{speedKph} km/h</div>
        </div>
        <div className="stat">
          <div className="k">TTC</div>
          <div className={`v ${ttcClass(ttc)}`}>
            {ttc === null || ttc === undefined ? "-" : `${ttc.toFixed(1)} s`}
          </div>
        </div>
        <div className="stat">
          <div className="k">SCORE</div>
          <div className="v">
            {episode ? episode.score.toFixed(0) : experiment?.score?.toFixed(0) ?? "-"}
          </div>
        </div>
        <div className="stat">
          <div className="k">COLLISION</div>
          <div className={`v ${episode?.collision ? "bad" : "good"}`}>
            {episode ? (episode.collision ? "YES" : "NO") : "-"}
          </div>
        </div>
        <div className="stat">
          <div className="k">ROUTE</div>
          <div className="v">
            {tick?.route_pct !== undefined ? `${tick.route_pct.toFixed(0)}%` : "-"}
          </div>
        </div>
        <div className="stat">
          <div className="k">SIM TIME</div>
          <div className="v">{(tick?.sim_time ?? 0).toFixed(1)} s</div>
        </div>
      </div>

      {/* ---------------- timeline ---------------- */}
      <div className="timeline">
        <div className="label">TIMELINE</div>
        {events.length === 0 && <div className="placeholder">no events yet</div>}
        {events.map((event, index) => (
          <div className="event" key={`${event.time}-${index}`}>
            <span className="t">{event.time.toFixed(2)}s</span>
            <span
              className={
                event.type === "COLLISION"
                  ? "bad"
                  : event.type.startsWith("CUT_IN")
                    ? "warn"
                    : ""
              }
            >
              {event.type}
            </span>
            <span className="muted">
              {Object.entries(event.data)
                .map(([k, v]) => `${k}=${v}`)
                .join(" ")}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
