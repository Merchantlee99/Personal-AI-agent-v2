# Hermes Source Priority (n8n Schedule)

이 문서는 Hermes 수집 정책(P0/P1/P2, 카테고리, 포맷)을 정의합니다.
보안 전체 기준은 [SECURITY_BASELINE](SECURITY_BASELINE.md)을 따릅니다.

## 1) 수집 우선순위와 스케줄

| Tier | 목적 | 주기 | KST 시간 | n8n 노드 |
|---|---|---|---|---|
| P0 | 즉시성 높은 핵심 신호 | 매일 | 09:00 | `Schedule P0 Daily (KST 09:00)` |
| P1 | 분석 가치 높은 중간 신호 | 2일마다 | 09:10 | `Schedule P1 Every2Days (KST 09:10)` |
| P2 | 롱테일 관찰 신호 | 3일마다 | 09:20 | `Schedule P2 Every3Days (KST 09:20)` |

## 2) 카테고리 키 + 이모지

| category | label | emoji |
|---|---|---|
| `kr_super_app` | KR Super App/PM | 📱 |
| `kr_engineering_core` | KR Engineering Core | 🛠️ |
| `kr_mobility` | KR Mobility/Travel | 🚗 |
| `kr_ai_growth` | KR AI/Growth | 🤖 |
| `kr_aggregator` | KR Curation | 🧭 |
| `global_bigtech` | Global BigTech | 🌐 |
| `global_ai` | Global AI | 🧠 |
| `global_strategy` | Global Product/Strategy | 📈 |
| `global_aggregator` | Global Aggregator | 🗞️ |

## 3) Tier별 대표 소스

### P0 (매일)
- Toss Tech, Naver D2, Kakao Tech, GeekNews
- OpenAI News, Anthropic News
- Cloudflare Blog, AWS News Blog, Hacker News

### P1 (2일마다)
- Woowahan Tech, Karrot Tech
- MyRealTrip Tech, Socar Tech
- Upstage, AB180
- Netflix, Airbnb, Uber
- Hugging Face, DeepMind
- Stratechery, Lenny's Newsletter

### P2 (3일마다)
- LINE Engineering
- Yanolja Tech, Tmap Mobility
- Hyperconnect
- Stripe, Reforge
- InfoQ, TechCrunch

## 4) 데이터 계약(브리핑 전 필수 필드)

Hermes 수집 결과는 최소 아래 필드를 유지합니다.
- `title`
- `url`
- `snippet`
- `category`
- `priorityTier`
- `publisher`
- `domain`
- `security_stats`

## 5) 보안 필터 정책

필수 필터
- `INJECTION_PATTERNS`: prompt-like 문구 제거
- `isSafeUrl`: unsafe/internal URL 차단
- `security_stats`: 제거/드롭 통계 기록

적용 워크플로
- `n8n/workflows/hermes-daily-briefing.json`
- `n8n/workflows/hermes-web-search-tavily.json`

## 6) Telegram 브리핑 포맷 기준

고정 섹션
- `🧩 주제`
- `📌 핵심 요약`
- `🔎 출처`
- `🧠 Minerva 인사이트`

금지 포맷
- `##` heading
- `**` bold

## 7) Tier별 톤/길이

| Tier | 톤 | 요약 | 인사이트 | 출처 |
|---|---|---|---|---|
| P0 | 즉시 실행형 | 2줄 | 1줄 | 최대 2개 |
| P1 | 분석형 | 3줄 | 2줄 | 최대 3개 |
| P2 | 스캔형 | 2줄 | 2줄 | 최대 2개 |

## 8) 한국어 번역 최적화(DeepL)

비용 절감 정책
- P0: `summary` + 상위 2개 `source snippet`
- P1: `summary` + 상위 1개 `source snippet`
- P2: 자동 번역 없음

원칙
- 제목/출처 타이틀은 원문 유지(문자량 절감)
- 번역 실패 시 원문 유지(브리핑 중단 금지)

## 9) 운영 팁
- `HERMES_SEARCH_PROVIDER=tavily`면 Tavily 강제, `auto`면 키 존재 시 Tavily 우선
- 소스 오염이 의심되면 `security_stats.prompt_like_removed`, `dropped_unsafe_url` 먼저 확인

