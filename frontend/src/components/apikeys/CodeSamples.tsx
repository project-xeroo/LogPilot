import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/shared/overlays";
import { Input, Label } from "@/components/shared/ui";
import { cn } from "@/utils/cn";

const EXAMPLE_RECORD = `{"message": "checkout failed", "severity": "ERROR", "service": "my-service", "attributes": {"error_code": "CHECKOUT_FAILED"}}`;

function buildSnippets(baseUrl: string, projectId: string, apiKey: string) {
  const url = `${baseUrl}/api/v1/projects/${projectId}/logs/records`;
  return {
    curl: `curl -X POST "${url}" \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"records": [${EXAMPLE_RECORD}]}'`,
    python: `import requests

requests.post(
    "${url}",
    headers={"X-API-Key": "${apiKey}"},
    json={"records": [${EXAMPLE_RECORD}]},
)`,
    node: `await fetch("${url}", {
  method: "POST",
  headers: { "X-API-Key": "${apiKey}", "Content-Type": "application/json" },
  body: JSON.stringify({ records: [${EXAMPLE_RECORD}] }),
});`,
    go: `req, _ := http.NewRequest("POST", "${url}", strings.NewReader(
    \`{"records": [${EXAMPLE_RECORD}]}\`))
req.Header.Set("X-API-Key", "${apiKey}")
req.Header.Set("Content-Type", "application/json")
http.DefaultClient.Do(req)`,
  };
}

function CopyBlock({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard permission denied - the text is still selectable */ }
  };
  return (
    <div className="group relative">
      <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-all rounded-control border border-line bg-raised p-3 pr-11 font-mono text-[12.5px] leading-relaxed">{value}</pre>
      <button
        type="button" onClick={copy} aria-label="Copy code"
        className={cn("absolute right-2 top-2 rounded-control p-1.5 text-muted hover:bg-ink/[.08] hover:text-ink", copied && "text-go")}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </div>
  );
}

/**
 * Copy-paste integration snippets for a project's API key, in whichever language a visiting engineer
 * actually writes. `apiKey` is either a real just-created key (shown once, in the create-key dialog) or
 * the placeholder "YOUR_API_KEY" for the page's permanent reference copy - either way the base URL and
 * project id are pre-filled from where this page is actually running, not left as generic examples, and
 * the base URL stays editable in case LogPilot is reached from a different address than this browser is.
 */
export function CodeSamples({ projectId, apiKey }: { projectId: string; apiKey: string }) {
  const [baseUrl, setBaseUrl] = useState(() => window.location.origin);
  const snippets = buildSnippets(baseUrl || window.location.origin, projectId, apiKey);

  return (
    <div className="space-y-3">
      <div className="max-w-md">
        <Label htmlFor="sample-base-url">Base URL</Label>
        <Input id="sample-base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={window.location.origin} />
        <p className="mt-1 text-xs text-muted">Defaults to where you're viewing this from - change it if the app sending logs reaches LogPilot at a different address.</p>
      </div>
      <Tabs defaultValue="curl">
        <TabsList>
          <TabsTrigger value="curl">cURL</TabsTrigger>
          <TabsTrigger value="python">Python</TabsTrigger>
          <TabsTrigger value="node">Node.js</TabsTrigger>
          <TabsTrigger value="go">Go</TabsTrigger>
        </TabsList>
        <TabsContent value="curl"><CopyBlock value={snippets.curl} /></TabsContent>
        <TabsContent value="python"><CopyBlock value={snippets.python} /></TabsContent>
        <TabsContent value="node"><CopyBlock value={snippets.node} /></TabsContent>
        <TabsContent value="go"><CopyBlock value={snippets.go} /></TabsContent>
      </Tabs>
    </div>
  );
}
