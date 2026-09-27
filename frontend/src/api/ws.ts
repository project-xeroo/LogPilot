import { useLive } from "@/store/live";
import { useSession } from "@/store/session";
import type { LiveEvent } from "@/types";

type Listener = (e: LiveEvent) => void;

/** Native WebSocket wrapped with reconnect + backoff and a tiny pub/sub. One socket serves the whole console. */
class LiveSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private retry = 0;
  private timer: number | undefined;
  private ping: number | undefined;
  private wantOpen = false;

  connect() {
    const token = useSession.getState().token;
    if (!token) return;
    this.wantOpen = true;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    useLive.getState().setStatus("connecting");
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      useLive.getState().setStatus("open");
      const p = useSession.getState().projectId;
      if (p) this.send({ type: "focus", project_id: p });
      window.clearInterval(this.ping);
      this.ping = window.setInterval(() => this.send({ type: "ping" }), 25000);
    };
    ws.onmessage = (m) => {
      try {
        const e = JSON.parse(m.data) as LiveEvent;
        this.listeners.forEach((l) => l(e));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = (ev) => {
      window.clearInterval(this.ping);
      useLive.getState().setStatus("closed");
      if (ev.code === 4401) {
        useSession.getState().logout();
        return;
      }
      if (this.wantOpen) {
        this.timer = window.setTimeout(() => this.connect(), Math.min(1000 * 2 ** this.retry++, 15000));
      }
    };
    ws.onerror = () => ws.close();
  }

  disconnect() {
    this.wantOpen = false;
    window.clearTimeout(this.timer);
    window.clearInterval(this.ping);
    this.ws?.close();
    this.ws = null;
  }

  get isOpen() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  send(msg: unknown): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  subscribe(l: Listener) {
    this.listeners.add(l);
    return () => { this.listeners.delete(l); };
  }
}

export const live = new LiveSocket();
