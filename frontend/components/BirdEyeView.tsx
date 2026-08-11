"use client";

// Bird-eye view drawn on a canvas from the telemetry we already stream
// (spec section 55). Rendering a second CARLA camera for this would cost GPU
// time and bandwidth for something two rectangles and a trail can express.

import { useEffect, useRef } from "react";
import type { Tick } from "../lib/api";

const METRES_VISIBLE = 42; // half-height of the view, in metres

export default function BirdEyeView({
  tick,
  trail,
}: {
  tick: Tick | null;
  trail: { x: number; y: number }[];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = canvas;
    ctx.fillStyle = "#05070a";
    ctx.fillRect(0, 0, width, height);

    if (!tick || tick.x === undefined || tick.y === undefined) {
      ctx.fillStyle = "#8b98ab";
      ctx.font = "12px monospace";
      ctx.fillText("waiting for telemetry", 14, 22);
      return;
    }

    const scale = height / (METRES_VISIBLE * 2);
    const yaw = ((tick.yaw ?? 0) * Math.PI) / 180;

    // World -> screen, with the ego fixed at centre and pointing up. The
    // world rotates around the car, which is what a driver's map looks like.
    const toScreen = (wx: number, wy: number): [number, number] => {
      const dx = wx - (tick.x as number);
      const dy = wy - (tick.y as number);
      const fx = dx * Math.cos(yaw) + dy * Math.sin(yaw); // forward
      const fy = -dx * Math.sin(yaw) + dy * Math.cos(yaw); // right
      return [width / 2 + fy * scale, height / 2 - fx * scale];
    };

    // Distance rings, every 20 m.
    ctx.strokeStyle = "#1b2230";
    ctx.lineWidth = 1;
    for (let r = 20; r <= METRES_VISIBLE; r += 20) {
      ctx.beginPath();
      ctx.arc(width / 2, height / 2, r * scale, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Where the ego has been.
    if (trail.length > 1) {
      ctx.strokeStyle = "#2a4a6a";
      ctx.lineWidth = 2;
      ctx.beginPath();
      trail.forEach((point, index) => {
        const [sx, sy] = toScreen(point.x, point.y);
        index === 0 ? ctx.moveTo(sx, sy) : ctx.lineTo(sx, sy);
      });
      ctx.stroke();
    }

    const drawCar = (
      sx: number,
      sy: number,
      rotation: number,
      colour: string,
      label: string,
    ) => {
      const carLength = 4.6 * scale;
      const carWidth = 2.0 * scale;
      ctx.save();
      ctx.translate(sx, sy);
      ctx.rotate(rotation);
      ctx.fillStyle = colour;
      ctx.fillRect(-carWidth / 2, -carLength / 2, carWidth, carLength);
      // A nose marker, so heading is readable at a glance.
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(-carWidth / 2, -carLength / 2, carWidth, 2);
      ctx.restore();
      ctx.fillStyle = colour;
      ctx.font = "10px monospace";
      ctx.fillText(label, sx + carWidth, sy - carLength / 2 - 4);
    };

    if (tick.npc_x !== undefined && tick.npc_y !== undefined) {
      const [nx, ny] = toScreen(tick.npc_x, tick.npc_y);
      const relativeYaw = (((tick.npc_yaw ?? 0) - (tick.yaw ?? 0)) * Math.PI) / 180;
      drawCar(nx, ny, relativeYaw, "#ffc857", "NPC");
    }

    drawCar(width / 2, height / 2, 0, "#4da3ff", "EGO");

    ctx.fillStyle = "#8b98ab";
    ctx.font = "10px monospace";
    ctx.fillText(`${METRES_VISIBLE} m`, 10, height / 2 - METRES_VISIBLE * scale + 12);
  }, [tick, trail]);

  return <canvas ref={canvasRef} width={720} height={420} />;
}
