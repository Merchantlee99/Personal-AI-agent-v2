const DEEPL_ENDPOINT = "https://api-free.deepl.com/v2/translate";
const TRANSLATION_CACHE_LIMIT = 256;
const translationCache = new Map<string, string>();

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

export async function translateToKorean(text: string): Promise<string> {
  const raw = text.trim();
  if (!shouldTranslateToKorean(raw)) {
    return text;
  }

  const apiKey = (process.env.DEEPL_API_KEY ?? "").trim();
  if (!apiKey) {
    return text;
  }

  const cacheKey = `ko:${raw}`;
  const cached = cacheGet(cacheKey);
  if (cached) {
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
      return text;
    }
    const payload = (await response.json()) as {
      translations?: Array<{ text?: string }>;
    };
    const translated = payload.translations?.[0]?.text?.trim();
    if (!translated) {
      return text;
    }
    cacheSet(cacheKey, translated);
    return translated;
  } catch {
    return text;
  }
}
