"use client";

// The model's current action, drawn so it can actually be read.
//
// Throttle and brake use their full 0..1 range, so a plain bar is fine. Steering
// does not: on a highway the expert steers about 0.03 out of a possible 1.0, so
// a full-scale bar moves 1.5% and looks broken. Measured across the three
// shipped models, steering never left +/-0.05.
//
// So steering gets a centre-origin bar on a zoomed scale that widens only when
// the model actually asks for more. Small corrections are visible; a hard swerve
// still fits.

const STEER_SCALES = [0.15, 0.4, 1.0];

function steerScale(value: number): number {
  const magnitude = Math.abs(value);
  return STEER_SCALES.find((scale) => magnitude <= scale) ?? 1.0;
}

export default function ActionBars({
  throttle = 0,
  brake = 0,
  steer = 0,
}: {
  throttle?: number;
  brake?: number;
  steer?: number;
}) {
  const scale = steerScale(steer);
  // Fraction of half-width, clamped so a NaN or out-of-range value cannot
  // draw outside the track.
  const steerFraction = Math.max(-1, Math.min(1, (steer || 0) / scale));

  return (
    <>
      {(
        [
          ["THROTTLE", throttle, "var(--good)"],
          ["BRAKE", brake, "var(--bad)"],
        ] as [string, number, string][]
      ).map(([name, value, colour]) => (
        <div key={name} style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="muted" style={{ fontSize: 11 }}>
              {name}
            </span>
            <span style={{ fontSize: 12 }}>{(value || 0).toFixed(2)}</span>
          </div>
          <div className="bar">
            <div
              style={{
                width: `${Math.max(0, Math.min(1, value || 0)) * 100}%`,
                background: colour,
              }}
            />
          </div>
        </div>
      ))}

      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span className="muted" style={{ fontSize: 11 }}>
            STEER
          </span>
          <span style={{ fontSize: 12 }}>
            {(steer || 0).toFixed(3)}
            <span className="muted" style={{ fontSize: 10 }}> ±{scale}</span>
          </span>
        </div>
        {/* Centre-origin: the fill grows left or right from the middle. */}
        <div className="bar" style={{ position: "relative" }}>
          <div
            style={{
              position: "absolute",
              left: "50%",
              top: 0,
              bottom: 0,
              width: 1,
              background: "var(--line)",
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: steerFraction >= 0 ? "50%" : `${50 + steerFraction * 50}%`,
              width: `${Math.abs(steerFraction) * 50}%`,
              background: "var(--accent)",
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 9,
            marginTop: 2,
          }}
          className="muted"
        >
          <span>LEFT</span>
          <span>RIGHT</span>
        </div>
      </div>
    </>
  );
}
