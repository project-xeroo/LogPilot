// Generates base/deployments.yaml, services.yaml, hpa.yaml and pdb.yaml - one workload per PRD 12.2 component.
//   node infra/k8s/gen-manifests.js
// Edit this file (not the generated YAML) to change replicas, resources or commands.
const fs = require("fs");
const path = require("path");

const NS = "logpilot";
const IMG = (n) => `logpilot/${n}:2.0.0`;

const svcs = [
  { n: "api-gateway", img: "api-gateway", port: 8000, rep: 2, cpu: ["250m", "1"], mem: ["384Mi", "1Gi"], hpa: [2, 10], http: true },
  { n: "log-ingestion-service", img: "log-ingestion-service", port: 8001, rep: 2, cpu: ["250m", "1"], mem: ["384Mi", "1Gi"], hpa: [2, 8], http: true },
  { n: "ingestion-worker", img: "log-ingestion-service", rep: 2, cpu: ["500m", "2"], mem: ["512Mi", "2Gi"], hpa: [2, 20],
    cmd: ["celery", "-A", "app.worker:celery_app", "worker", "-Q", "ingestion", "--loglevel", "INFO", "--concurrency", "2"] },
  { n: "processing-worker", img: "processing-worker", rep: 2, cpu: ["500m", "2"], mem: ["768Mi", "3Gi"], hpa: [2, 30] },
  { n: "ai-service", img: "ai-service", port: 8002, rep: 2, cpu: ["250m", "1"], mem: ["384Mi", "1Gi"], hpa: [2, 12], http: true, egress: true },
  { n: "forecasting-service", img: "forecasting-service", port: 8003, rep: 2, cpu: ["250m", "1"], mem: ["384Mi", "1Gi"], hpa: [2, 6], http: true },
  { n: "forecasting-worker", img: "forecasting-service", rep: 2, cpu: ["500m", "2"], mem: ["512Mi", "2Gi"], hpa: [2, 20],
    cmd: ["celery", "-A", "app.loop.scheduler:celery_app", "worker", "-Q", "forecasting", "--loglevel", "INFO", "--concurrency", "4"] },
  // the scheduler is a singleton (Celery beat): Recreate so two copies never overlap during a rollout
  { n: "forecasting-scheduler", img: "forecasting-service", rep: 1, cpu: ["100m", "500m"], mem: ["256Mi", "512Mi"], recreate: true,
    cmd: ["celery", "-A", "app.loop.scheduler:celery_app", "beat", "--loglevel", "INFO", "--schedule", "/tmp/celerybeat-schedule"] },
  { n: "notification-service", img: "notification-service", port: 8004, rep: 2, cpu: ["100m", "500m"], mem: ["256Mi", "512Mi"], http: true },
  { n: "audit-service", img: "audit-service", port: 8005, rep: 2, cpu: ["100m", "500m"], mem: ["256Mi", "512Mi"], http: true },
  { n: "console", img: "console", port: 80, rep: 2, cpu: ["50m", "250m"], mem: ["64Mi", "128Mi"], http: true, health: "/healthz", noenv: true, root: true },
];

// ---- tiny YAML emitter (block style) -------------------------------------------------------------------------------
const scalar = (v) => (typeof v === "string" && /^[A-Za-z][\w./-]*$/.test(v) && !/^(true|false|null|yes|no|on|off)$/i.test(v) ? v : JSON.stringify(v));
function toYaml(o, ind = 0) {
  const pad = "  ".repeat(ind);
  if (Array.isArray(o)) {
    return o.map((v) => {
      if (v !== null && typeof v === "object") return pad + "- " + toYaml(v, ind + 1).slice((ind + 1) * 2);
      return pad + "- " + scalar(v);
    }).join("\n");
  }
  return Object.entries(o).filter(([, v]) => v !== undefined).map(([k, v]) => {
    if (v !== null && typeof v === "object") {
      const empty = Array.isArray(v) ? v.length === 0 : Object.keys(v).length === 0;
      return empty ? `${pad}${k}: ${Array.isArray(v) ? "[]" : "{}"}` : `${pad}${k}:\n${toYaml(v, ind + 1)}`;
    }
    return `${pad}${k}: ${scalar(v)}`;
  }).join("\n");
}

let dep = "", svc = "", hpa = "", pdb = "";
for (const s of svcs) {
  const labels = { "app.kubernetes.io/name": s.n, "app.kubernetes.io/part-of": "logpilot" };
  const probe = (extra) => ({ httpGet: { path: s.health || "/health", port: "http" }, periodSeconds: 10, failureThreshold: 3, ...extra });
  const container = {
    name: s.n, image: IMG(s.img), imagePullPolicy: "IfNotPresent",
    command: s.cmd, ports: s.port ? [{ containerPort: s.port, name: "http" }] : undefined,
    envFrom: s.noenv ? undefined : [{ configMapRef: { name: "logpilot-config" } }, { secretRef: { name: "logpilot-secrets" } }],
    resources: { requests: { cpu: s.cpu[0], memory: s.mem[0] }, limits: { cpu: s.cpu[1], memory: s.mem[1] } },
    securityContext: { allowPrivilegeEscalation: false, runAsNonRoot: !s.root, capabilities: { drop: ["ALL"] } },
    readinessProbe: s.http ? probe({}) : undefined,
    livenessProbe: s.http ? probe({ initialDelaySeconds: 20, periodSeconds: 20, failureThreshold: 5 }) : undefined,
  };
  const d = {
    apiVersion: "apps/v1", kind: "Deployment", metadata: { name: s.n, namespace: NS, labels },
    spec: {
      replicas: s.rep, selector: { matchLabels: { "app.kubernetes.io/name": s.n } },
      strategy: s.recreate ? { type: "Recreate" } : { rollingUpdate: { maxUnavailable: 0, maxSurge: 1 } },
      template: {
        metadata: { labels: { ...labels, ...(s.egress ? { "logpilot/egress-ai": "allowed" } : {}) } },
        spec: {
          serviceAccountName: "logpilot", automountServiceAccountToken: false, terminationGracePeriodSeconds: s.cmd || !s.http ? 60 : 30,
          topologySpreadConstraints: s.rep > 1 ? [{ maxSkew: 1, topologyKey: "topology.kubernetes.io/zone", whenUnsatisfiable: "ScheduleAnyway", labelSelector: { matchLabels: { "app.kubernetes.io/name": s.n } } }] : undefined,
          containers: [container],
        },
      },
    },
  };
  dep += "---\n" + toYaml(d) + "\n";
  if (s.port) svc += "---\n" + toYaml({ apiVersion: "v1", kind: "Service", metadata: { name: s.n, namespace: NS, labels }, spec: { selector: { "app.kubernetes.io/name": s.n }, ports: [{ name: "http", port: s.port, targetPort: "http" }] } }) + "\n";
  if (s.hpa) hpa += "---\n" + toYaml({
    apiVersion: "autoscaling/v2", kind: "HorizontalPodAutoscaler", metadata: { name: s.n, namespace: NS },
    spec: { scaleTargetRef: { apiVersion: "apps/v1", kind: "Deployment", name: s.n }, minReplicas: s.hpa[0], maxReplicas: s.hpa[1],
      metrics: [{ type: "Resource", resource: { name: "cpu", target: { type: "Utilization", averageUtilization: 65 } } }],
      behavior: { scaleDown: { stabilizationWindowSeconds: 300 } } },
  }) + "\n";
  if (s.rep > 1) pdb += "---\n" + toYaml({ apiVersion: "policy/v1", kind: "PodDisruptionBudget", metadata: { name: s.n, namespace: NS }, spec: { minAvailable: 1, selector: { matchLabels: { "app.kubernetes.io/name": s.n } } } }) + "\n";
}
const banner = (what) => `# ${what}\n# Generated by infra/k8s/gen-manifests.js. Edit the generator, not this file.\n`;
const out = (f, body) => fs.writeFileSync(path.join(__dirname, "base", f), body);
out("deployments.yaml", banner("One Deployment per component of PRD Section 12.2 (api, worker, scheduler, console, supporting services)."));
fs.appendFileSync(path.join(__dirname, "base", "deployments.yaml"), dep);
out("services.yaml", banner("ClusterIP services for every HTTP component.") + svc);
out("hpa.yaml", banner("Elastic autoscaling of workers and inference concurrency with log volume (PRD 11.2).") + hpa);
out("pdb.yaml", banner("Keep at least one replica through node drains and upgrades.") + pdb);
console.log(`wrote ${svcs.length} workloads`);
