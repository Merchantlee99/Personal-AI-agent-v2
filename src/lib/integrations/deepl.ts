import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const DEEPL_ENDPOINT = "https://api-free.deepl.com/v2/translate";
const TRANSLATION_CACHE_LIMIT = 256;
const translationCache = new Map<string, string>();
const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const DEEPL_USAGE_PATH = path.join(ROOT, "logs", "deepl_usage_metrics.json");

type DeepLUsageDelta = {
  attempts?: number;
  translated?: number;
  cached?: number;
  skipped?: number;
  failed?: number;
  input_chars?: number;
  translated_chars?: number;
};

function isKoreanText(value: string): boolean {
  return /[가-힣]/.test(value);
}

function hasLatinText(value: string): boolean {
  return /[A-Za-z]/.test(value);
}

export function shouldTranslateToKorean(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  if (isKoreanText(trimmed)) {
    return false;
  }
  return hasLatinText(trimmed);
}

function cacheGet(key: string): string | undefined {
  const found = translationCache.get(key);
  if (found === undefined) {
    return undefined;
  }
  translationCache.delete(key);
  translationCache.set(key, found);
  return found;
}

function cacheSet(key: string, value: string) {
  if (translationCache.size >= TRANSLATION_CACHE_LIMIT) {
    const oldest = translationCache.keys().next().value;
    if (typeof oldest === "string") {
      translationCache.delete(oldest);
    }
  }
  translationCache.set(key, value);
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

async function recordDeepLUsage(delta: DeepLUsageDelta) {
  try {
    await fs.mkdir(path.dirname(DEEPL_USAGE_PATH), { recursive: true });

    let data: { daily?: Record<string, Record<string, number>>; updated_at?: string } = {};
    try {
      const raw = await fs.readFile(DEEPL_USAGE_PATH, "utf-8");
      data = raw.trim() ? (JSON.parse(raw) as typeof data) : {};
    } catch {
      data = {};
    }

    if (!data.daily || typeof data.daily !== "object") {
      data.daily = {};
    }
    const day = todayKey();
    const entry = data.daily[day] ?? {
      attempts: 0,
      translated: 0,
      cached: 0,
      skipped: 0,
      failed: 0,
      input_chars: 0,
      translated_chars: 0,
    };

    for (const [key, value] of Object.entries(delta)) {
      const safe = Number.isFinite(value) ? Number(value) : 0;
      entry[key] = Number(entry[key] ?? 0) + safe;
    }
    data.daily[day] = entry;
    data.updated_at = new Date().toISOString();

    const tmpPath = `${DEEPL_USAGE_PATH}.${process.pid}.${crypto.randomUUID().slice(0, 8)}.tmp`;
    await fs.writeFile(tmpPath, JSON.stringify(data, null, 2), "utf-8");
    await fs.rename(tmpPath, DEEPL_USAGE_PATH);
  } catch {
    // non-blocking: metrics write should never fail translation flow
  }
}

export async function translateToKorean(text: string): Promise<string> {
  const raw = text.trim();
  if (!shouldTranslateToKorean(raw)) {
    void recordDeepLUsage({ skipped: 1 });
    return text;
  }

  const apiKey = (process.env.DEEPL_API_KEY ?? "").trim();
  if (!apiKey) {
    void recordDeepLUsage({ skipped: 1 });
    return text;
  }

  const cacheKey = `ko:${raw}`;
  const cached = cacheGet(cacheKey);
  if (cached) {
    void recordDeepLUsage({ cached: 1 });
    return cached;
  }

  const body = new URLSearchParams();
  body.set("text", raw);
  body.set("target_lang", (process.env.DEEPL_TARGET_LANG ?? "KO").trim().toUpperCase() || "KO");
  const glossaryId = (process.env.DEEPL_GLOSSARY_ID ?? "").trim();
  if (glossaryId) {
    body.set("glossary_id", glossaryId);
  }

  try {
    void recordDeepLUsage({ attempts: 1, input_chars: raw.length });
    const response = await fetch(DEEPL_ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `DeepL-Auth-Key ${apiKey}`,
        "content-type": "application/x-www-form-urlencoded",
      },
      body: body.toString(),
      cache: "no-store",
    });
    if (!response.ok) {
      void recordDeepLUsage({ failed: 1 });
      return text;
    }
    const payload = (await response.json()) as {
      translations?: Array<{ text?: string }>;
    };
    const translated = payload.translations?.[0]?.text?.trim();
    if (!translated) {
      void recordDeepLUsage({ failed: 1 });
      return text;
    }
    cacheSet(cacheKey, translated);
    void recordDeepLUsage({ translated: 1, translated_chars: translated.length });
    return translated;
  } catch {
    void recordDeepLUsage({ failed: 1 });
    return text;
  }
}
