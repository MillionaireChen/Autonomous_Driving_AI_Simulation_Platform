"use client";

// Replay of a finished episode (spec section 58).
//
// The live dashboard is driven by a socket; this page is driven by an index.
// Everything else - the bird-eye view, the action bars, the timeline - is the
// same component reading the same tick shape, because a recorded tick and a
// live tick are the same thing.

import { use, useCallback, useEffect, useRef, useState } from "react";
import BirdEyeView from "../../../components/BirdEyeView";
import { API_BASE, type Tick } from "../../../lib/api";

type Replay = {
  experiment_id: string;
  scenario_id: string;
  model_id: string;
  seed: number;
  result: string;
  score: number;
  termination_reason: string;
  ticks: number;
  duration_s: number;
  has_frames: boolean;
  frames: { index: number; tick: number; sim_time: number }[];
  telemetry: Tick[];
  events: { time: number; type: string; data: Record<string, unknown> }[];
};

const SPEEDS = [0.5, 1, 2, 4];

export default function ReplayPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/experiments/${id}/replay`)
      .then((r) => (r.ok ? r.json() : r.text().then((t) => Promise.reject(t))))
      .then(setReplay)
      .catch((e) => setError(String(e)));
  }, [id]);

  // The recording is 20 Hz, so a tick every 50 ms is real time.
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (!playing || !replay) return;
    timer.current = setInterval(() => {
      setCursor((c) => {
        if (c >= replay.telemetry.length - 1) {
          setPlaying(false);
          return c;
        }
        return c + 1;
      });
    }, 50 / rate);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, rate, replay]);

  const tick: Tick | null = replay?.telemetry[cursor] ?? null;

  // Frames are recorded at 10 Hz against 20 Hz ticks, so hold the most recent
  // one rather than blanking the view on the ticks between.
  const frameIndex = useCallback(() => {
    if (!replay?.has_frames) return null;
    let found: number | null = null;
    for (const frame of replay.frames) {
      if (frame.tick <= cursor) found = frame.index;
      else break;
    }
    return found;
  }, [replay, cursor]);

  const trail = replay
    ? replay.telemetry
        .slice(Math.max(0, cursor - 400), cursor + 1)
        .filter((t) => t.x !== undefined)
        .map((t) => ({ x: t.x as number, y: t.y as number }))
    : [];

  const shown = replay?.events.filter((e) => e.time <= (tick?.sim_time ?? 0)) ?? [];
  const currentFrame = frameIndex();

  if (error) {
    return (
      <div className="app">
        <div className="topbar">
          <div className="title">REPLAY {id}</div>
        </div>
        <div className="panel bad">{error}</div>
      </div>
    );
  }

  if (!replay) {
    return (
      <div className="app">
        <div className="topbar">
          <div className="title">REPLAY {id}</div>
        </div>
        <div className="panel placeholder">loading…</div>
      </div>
    );
  }

  return (
    <div className="app">
      <div className="topbar">
        <div className="title">REPLAY · {replay.experiment_id}</div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="pill">{replay.model_id}</span>
          <span className="pill">{replay.scenario_id}</span>
          <span className="pill">seed {replay.seed}</span>
          <span className={`pill ${replay.result === "PASS" ? "good" : "bad"}`}>
            {replay.result} · {replay.score.toFixed(0)}
          </span>
          <a href="/" className="pill" style={{ textDecoration: "none" }}>
            LIVE
          </a>
        </div>
      </div>

      <div className="main">
        <div className="panel">
          <div className="label">PLAYBACK</div>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            <button onClick={() => setPlaying((p) => !p)}>
              {playing ? "PAUSE" : "PLAY"}
            </button>
            <button
              onClick={() => {
                setCursor(0);
                setPlaying(false);
              }}
            >
              RESET
            </button>
          </div>

          <div className="label">SPEED</div>
          <div style={{ display: "flex", gap: 6, marginBottom: 18 }}>
            {SPEEDS.map((s) => (
              <button
                key={s}
                onClick={() => setRate(s)}
                style={{
                  padding: "6px 10px",
                  borderColor: rate === s ? "var(--accent)" : undefined,
                }}
              >
                {s}x
              </button>
            ))}
          </div>

          <div style={{ fontSize: 12, lineHeight: 1.9 }}>
            <div>
              tick {cursor} / {replay.telemetry.length - 1}
            </div>
            <div>t = {(tick?.sim_time ?? 0).toFixed(2)} s</div>
            <div className="muted">{replay.termination_reason}</div>
            {!replay.has_frames && (
              <div className="warn" style={{ marginTop: 10 }}>
                no frames recorded for this run
              </div>
            )}
          </div>
        </div>

        <div className="center">
          <div className="view">
            <div className="view-label">FRONT CAMERA</div>
            {currentFrame !== null ? (
              <img
                src={`${API_BASE}/api/experiments/${id}/frames/${currentFrame}`}
                alt="front camera"
              />
            ) : (
              <div className="placeholder">
                {replay.has_frames ? "…" : "camera was not recorded"}
              </div>
            )}
          </div>
          <div className="view">
            <div className="view-label">BIRD-EYE VIEW</div>
            <BirdEyeView tick={tick} trail={trail} />
          </div>
        </div>

        <div className="panel">
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

          <div style={{ marginTop: 20, fontSize: 12, lineHeight: 1.8 }}>
            <div className="label">SCENARIO VEHICLE</div>
            <div>
              gap{" "}
              {tick?.npc_gap_m === undefined ? "-" : `${tick.npc_gap_m.toFixed(1)} m`}
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

      <div className="statusbar">
        <div className="stat">
          <div className="k">SPEED</div>
          <div className="v">{((tick?.speed_mps ?? 0) * 3.6).toFixed(0)} km/h</div>
        </div>
        <div className="stat">
          <div className="k">TTC</div>
          <div className="v">
            {tick?.ttc_s === null || tick?.ttc_s === undefined
              ? "-"
              : `${tick.ttc_s.toFixed(1)} s`}
          </div>
        </div>
        <div className="stat">
          <div className="k">ROUTE</div>
          <div className="v">
            {tick?.route_pct !== undefined ? `${tick.route_pct.toFixed(0)}%` : "-"}
          </div>
        </div>
        <div className="stat" style={{ gridColumn: "span 3" }}>
          <div className="k">SCRUB</div>
          <input
            type="range"
            min={0}
            max={Math.max(0, replay.telemetry.length - 1)}
            value={cursor}
            onChange={(e) => {
              setPlaying(false);
              setCursor(Number(e.target.value));
            }}
            style={{ marginTop: 6 }}
          />
        </div>
      </div>

      <div className="timeline">
        <div className="label">TIMELINE</div>
        {shown.length === 0 && <div className="placeholder">no events yet</div>}
        {shown.map((event, index) => (
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
