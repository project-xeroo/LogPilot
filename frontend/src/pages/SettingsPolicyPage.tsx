import { useSearchParams } from "react-router-dom";
import { AuditPanel } from "@/components/settings/AuditPanel";
import { ForecastSettings } from "@/components/settings/ForecastSettings";
import { MetricsPanel } from "@/components/settings/MetricsPanel";
import { PolicyTable } from "@/components/settings/PolicyTable";
import { ProviderPanel } from "@/components/settings/ProviderPanel";
import { WebhooksPanel } from "@/components/settings/WebhooksPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/shared/overlays";
import { PageHeader } from "@/components/shared/ui";
import { can, useSession } from "@/store/session";

/** Settings - Autonomy & Policy: per-tool autonomy tiers, alert thresholds, model/provider settings. */
export default function SettingsPolicyPage() {
  const user = useSession((s) => s.user);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "autonomy";
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Autonomy and policy">Decide how much the agent may do on its own, when it should warn you, and which AI provider it uses.</PageHeader>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="autonomy">Autonomy tiers</TabsTrigger>
          <TabsTrigger value="alerts">Thresholds and services</TabsTrigger>
          <TabsTrigger value="provider">AI provider</TabsTrigger>
          {can(user, "webhooks.manage") && <TabsTrigger value="webhooks">Webhooks</TabsTrigger>}
          {can(user, "audit.view") && <TabsTrigger value="audit">Audit trail</TabsTrigger>}
          <TabsTrigger value="metrics">Success metrics</TabsTrigger>
        </TabsList>
        <TabsContent value="autonomy"><PolicyTable /></TabsContent>
        <TabsContent value="alerts"><ForecastSettings /></TabsContent>
        <TabsContent value="provider"><ProviderPanel /></TabsContent>
        <TabsContent value="webhooks"><WebhooksPanel /></TabsContent>
        <TabsContent value="audit"><AuditPanel /></TabsContent>
        <TabsContent value="metrics"><MetricsPanel /></TabsContent>
      </Tabs>
    </div>
  );
}
