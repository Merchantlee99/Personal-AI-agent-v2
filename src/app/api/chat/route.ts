import crypto from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { normalizeAgentId } from "@/lib/agents";
import { readAgentMemoryContext } from "@/lib/orchestration/memory-context";

type ChatRequestBody = {
  agentId?: string;
  message?: string;
  history?: Array<{ role: string; text?: string; content?: string; at?: string }>;
};

const createSignature = (secret: string, payload: string) =>
  crypto.createHmac("sha256", secret).update(payload).digest("hex");

const parseCsv = (raw: string) =>
  raw
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0);

export async function POST(req: NextRequest) {
  const body = (await req.json()) as ChatRequestBody;
  const normalizedId = normalizeAgentId(body.agentId ?? "");

  if (!normalizedId) {
    return NextResponse.json({ error: "Unknown agent id" }, { status: 400 });
  }

  const message = body.message?.trim();
  if (!message) {
    return NextResponse.json({ error: "message is required" }, { status: 400 });
  }

  const proxyUrl = process.env.LLM_PROXY_URL ?? "http://127.0.0.1:8001";
  const internalToken = process.env.INTERNAL_API_TOKEN ?? "change-me-in-env";
  const signingSecret = process.env.INTERNAL_SIGNING_SECRET ?? "change-signing-secret";
  const memoryAgents = parseCsv(process.env.CHAT_MEMORY_CONTEXT_AGENTS ?? "minerva");
  const memoryContext =
    memoryAgents.includes(normalizedId) && process.env.CHAT_MEMORY_CONTEXT_ENABLED !== "false"
      ? await readAgentMemoryContext(normalizedId, {
          maxChars: Number(process.env.CHAT_MEMORY_CONTEXT_MAX_CHARS ?? 900),
          maxBlocks: Number(process.env.CHAT_MEMORY_CONTEXT_MAX_BLOCKS ?? 4),
          maxItems: Number(process.env.CHAT_MEMORY_CONTEXT_MAX_ITEMS ?? 4),
          query: message,
        })
      : null;

  const requestPayload = JSON.stringify({
    agent_id: normalizedId,
    message,
    history: (body.history ?? []).map((item) => ({
      role: item.role,
      text: item.text ?? item.content ?? "",
      at: item.at ?? null,
    })),
    memory_context: memoryContext,
    source: "web",
  });

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID();
  const signatureBase = `${timestamp}.${nonce}.${requestPayload}`;
  const signature = createSignature(signingSecret, signatureBase);

  const response = await fetch(`${proxyUrl}/api/agent`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-internal-token": internalToken,
      "x-timestamp": timestamp,
      "x-nonce": nonce,
      "x-signature": signature,
    },
    body: requestPayload,
    cache: "no-store",
  });

  if (!response.ok) {
    const failure = await response.text();
    return NextResponse.json(
      { error: "proxy_error", detail: failure || "llm-proxy returned an error" },
      { status: response.status }
    );
  }

  const payload = await response.json();
  return NextResponse.json({
    agentId: payload.agent_id,
    model: payload.model,
    reply: payload.reply,
  });
}
