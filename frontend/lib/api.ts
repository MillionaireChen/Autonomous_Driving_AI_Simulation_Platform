// Client for the arena backend.

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export type Model = {
  id: string;
  name: string;
  type: string;
  endpoint: string;
  timeout_ms: number;
};

export type Scenario = {
  id: string;
  name: string;
  map: string;
  version: string;
  duration_seconds: number;
  default_seed: number;
};

export type Experiment = {
  id: string;
  model_id: string;
  scenario_id: string;
  seed: number;
  status: string;
  score: number | null;
  error: string | null;
  versions: Record<string, string>;
};

export type Episode = {
  id: number;
  experiment_id: string;
  collision: boolean;
  collision_count: number;
  minimum_ttc: number | null;
  route_completion: number;
  average_speed: number;
  max_speed: number;
  hard_brake_count: number;
  lane_invasion_count: number;
  model_latency_p50: number;
  model_latency_p95: number;
  model_timeouts: number;
  ticks: number;
  duration_s: number;
  distance_m: number;
  termination_reason: string;
  result: string;
  score: number;
};

/** One tick as it arrives over the WebSocket. */
export type Tick = {
  type?: string;
  sim_time?: number;
  speed_mps?: number;
  throttle?: number;
  steer?: number;
  brake?: number;
  x?: number;
  y?: number;
  yaw?: number;
  ttc_s?: number | null;
  route_pct?: number;
  npc_x?: number;
  npc_y?: number;
  npc_yaw?: number;
  npc_gap_m?: number;
  npc_speed_mps?: number;
  camera_front?: string;
  events?: { time: number; type: string; data: Record<string, unknown> }[];
};

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${response.status} ${detail}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  models: () => json<Model[]>("/api/models"),
  scenarios: () => json<Scenario[]>("/api/scenarios"),
  experiment: (id: string) => json<Experiment>(`/api/experiments/${id}`),
  experiments: () => json<Experiment[]>("/api/experiments"),
  episodes: (id: string) => json<Episode[]>(`/api/experiments/${id}/episodes`),
  createExperiment: (body: {
    model_id: string;
    scenario_id: string;
    seed: number;
  }) =>
    json<Experiment>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  start: (id: string) =>
    json<Experiment>(`/api/experiments/${id}/start`, { method: "POST" }),
  stop: (id: string) =>
    json<Experiment>(`/api/experiments/${id}/stop`, { method: "POST" }),
};

export function telemetryUrl(experimentId: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/ws/experiments/${experimentId}/telemetry`;
}
