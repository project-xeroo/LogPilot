import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

/** Tiny trend line; decorative (aria-hidden) because the numeric value is always shown next to it. */
export function Sparkline({ data, color = "hsl(var(--chart))", height = 28, domain }: { data: number[]; color?: string; height?: number; domain?: [number, number] }) {
  if (data.length < 2) return <div style={{ height }} aria-hidden />;
  const rows = data.map((v, i) => ({ i, v }));
  return (
    <div style={{ height }} aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <YAxis hide domain={domain ?? ["dataMin", "dataMax"]} />
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={color} fillOpacity={0.12} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
