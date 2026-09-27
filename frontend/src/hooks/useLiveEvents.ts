import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { live } from "@/api/ws";
import { useLive } from "@/store/live";
import { useSession } from "@/store/session";
import type { LiveEvent } from "@/types";

/**
 * Connects the console to the agent's event stream and turns events into fresh data:
 * the Agent Feed, Failure Risk Board and Alerts queue update live as the agent acts, and
 * proactive alerts / pre-mortems interrupt whatever screen the person is on.
 */
export function useLiveEvents() {
  const qc = useQueryClient();
  const token = useSession((s) => s.token);
  const projectId = useSession((s) => s.projectId);
  const push = useLive((s) => s.push);
  const setLast = useLive((s) => s.setLast);
  const pending = useRef<Record<string, number>>({});

  useEffect(() => {
    if (!token) return;
    live.connect();
    return () => live.disconnect();
  }, [token]);

  useEffect(() => {
    if (projectId) live.send({ type: "focus", project_id: projectId });
  }, [projectId]);

  useEffect(() => {
    // coalesce bursts (a risk cycle per service every minute) into one refetch
    const invalidate = (key: unknown[], wait = 1500) => {
      const k = JSON.stringify(key);
      if (pending.current[k]) return;
      pending.current[k] = window.setTimeout(() => {
        delete pending.current[k];
        qc.invalidateQueries({ queryKey: key });
      }, wait);
    };
    return live.subscribe((e: LiveEvent) => {
      if (e.project_id && projectId && e.project_id !== projectId) return;
      setLast(e);
      const pid = projectId;
      switch (e.type) {
        case "feed.item":
          invalidate(["feed", pid], 300);
          break;
        case "risk.updated":
          invalidate(["risk", pid]);
          invalidate(["health", pid], 4000);
          break;
        case "alert.created":
        case "alert.escalated":
        case "alert.pending_review": {
          const a = e.payload ?? e;
          push({
            key: `${e.type}:${a.id}:${a.level}`,
            kind: e.type === "alert.escalated" ? "escalated" : "alert",
            title: a.message?.title ?? `${a.service}: ${Math.round(a.risk_score ?? 0)}/100 failure risk`,
            body: a.text ?? "",
            service: a.service,
            alertId: a.id,
            level: a.level,
          });
          ["alerts", "approvals", "risk", "feed"].forEach((k) => invalidate([k, pid], 200));
          break;
        }
        case "alert.updated":
        case "alert.resolved":
        case "action.decided":
          ["alerts", "approvals", "alert"].forEach((k) => invalidate([k, pid], 200));
          break;
        case "report.pre_mortem": {
          const p = e.payload ?? e;
          push({ key: `pm:${p.report_id}`, kind: "pre_mortem", title: "A pre-mortem report is ready for review", body: p.title ?? "", reportId: p.report_id });
          invalidate(["reports", pid], 200);
          invalidate(["feed", pid], 200);
          break;
        }
        case "ingestion.progress":
          invalidate(["sessions", pid], 300);
          if (e.payload?.status === "completed") ["health", "risk", "clusters", "anomalies", "feed"].forEach((k) => invalidate([k, pid], 500));
          break;
        case "anomaly.detected":
          invalidate(["anomalies", pid]);
          break;
        case "deployment.regression":
          invalidate(["deployments", pid]);
          invalidate(["feed", pid], 300);
          break;
      }
    });
  }, [qc, projectId, push, setLast]);
}
