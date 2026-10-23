// Embeds text with gte-small (384 dimensions), running inside Supabase so the backend needs no ML libraries.
//   POST { "inputs": ["text", ...] }   headers: x-embed-key: <the project's secret/service key>
//   -> { "embeddings": [[...384 numbers], ...] }
// Callers are authenticated by proving they hold the project's secret key: the key is tried against a table that
// row-level security hides from every other key. Validated keys are remembered for 10 minutes.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const session = new Supabase.ai.Session("gte-small");
const validated = new Map<string, number>();
const MAX_BATCH = 32;
const MAX_CHARS = 2400;

async function authorized(key: string): Promise<boolean> {
  if (!key || key.length < 20) return false;
  const until = validated.get(key);
  if (until && until > Date.now()) return true;
  const base = Deno.env.get("SUPABASE_URL")!;
  const r = await fetch(`${base}/rest/v1/document_chunks?select=chunk_id&limit=1`, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
  });
  if (!r.ok) return false;
  const rows = await r.json();
  const ok = Array.isArray(rows) && rows.length > 0;
  if (ok) validated.set(key, Date.now() + 10 * 60 * 1000);
  return ok;
}

Deno.serve(async (req: Request) => {
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  if (!(await authorized(req.headers.get("x-embed-key") ?? ""))) return json({ error: "unauthorized" }, 401);
  let inputs: unknown;
  try { inputs = (await req.json()).inputs; } catch { return json({ error: "bad json" }, 400); }
  if (!Array.isArray(inputs) || inputs.length === 0 || inputs.length > MAX_BATCH || !inputs.every((x) => typeof x === "string")) {
    return json({ error: `inputs must be 1-${MAX_BATCH} strings` }, 400);
  }
  const embeddings: number[][] = [];
  for (const text of inputs as string[]) {
    const out = await session.run(text.slice(0, MAX_CHARS), { mean_pool: true, normalize: true });
    embeddings.push(Array.from(out as ArrayLike<number>));
  }
  return json({ embeddings });
});
