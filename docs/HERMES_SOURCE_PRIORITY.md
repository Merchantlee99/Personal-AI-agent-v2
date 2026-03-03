# Hermes Source Priority (n8n Schedule)

Hermes 스케줄 수집은 아래 기준으로 우선순위를 둔다.
이 문서는 Hermes 수집/브리핑 정책만 다루며, 전체 보안 통제는 `SECURITY_BASELINE.md`를 따른다.

## 1) Priority Tiers

### P0 (매일, 핵심)
- KR Super App/PM: Toss Tech
- KR Engineering Core: Naver D2, Kakao Tech
- KR Curation: GeekNews
- Global AI: OpenAI News, Anthropic News
- Global BigTech: Cloudflare Blog, AWS News Blog
- Global Aggregator: Hacker News

### P1 (주 3~7회)
- KR Super App/PM: Woowahan Tech, Karrot Tech
- KR Mobility/Travel: MyRealTrip Tech, Socar Tech
- KR AI/Growth: Upstage, AB180
- Global BigTech: Netflix TechBlog, Airbnb Engineering, Uber Engineering
- Global AI: Hugging Face Blog, Google DeepMind Blog
- Global Product/Strategy: Stratechery, Lenny's Newsletter

### P2 (주 1~3회)
- KR Super App/PM: LINE Engineering
- KR Mobility/Travel: Yanolja Tech, Tmap Mobility Tech
- KR AI/Growth: Hyperconnect
- Global BigTech: Stripe Blog
- Global Product/Strategy: Reforge Blog
- Global Aggregator: InfoQ, TechCrunch

## 1-1) n8n Schedule Mapping

- `P0`: 매일 1회 (`KST 09:00`, cron `0 0 * * *` UTC)
- `P1`: 2일마다 1회, 주 3회 수준 (`KST 09:10`, cron `10 0 */2 * *` UTC)
- `P2`: 3일마다 1회, 주 2회 수준 (`KST 09:20`, cron `20 0 */3 * *` UTC)

## 2) Category Keys

- `kr_super_app`
- `kr_engineering_core`
- `kr_mobility`
- `kr_ai_growth`
- `kr_aggregator`
- `global_bigtech`
- `global_ai`
- `global_strategy`
- `global_aggregator`

## 2-1) Category Emoji

- `kr_super_app`: 📱
- `kr_engineering_core`: 🛠️
- `kr_mobility`: 🚗
- `kr_ai_growth`: 🤖
- `kr_aggregator`: 🧭
- `global_bigtech`: 🌐
- `global_ai`: 🧠
- `global_strategy`: 📈
- `global_aggregator`: 🗞️

## 3) Data Contract (권장)

Hermes 수집 결과에 아래 필드를 유지:

- `title`
- `url`
- `snippet`
- `category`
- `priority_tier`
- `publisher`
- `domain`
- `security_stats` (injection 제거/unsafe url 제거 통계)

## 4) Telegram 전달 포맷 원칙

- markdown heading (`##`) 금지
- markdown bold (`**`) 금지
- 한 메시지 4,096자 이내
- 고정 섹션: 주제 / 핵심 요약 / 출처 / Minerva 인사이트
- 출처는 `P0~P2 + 카테고리`를 함께 표기

## 4-1) Tier별 길이/톤

- `P0`: 짧은형(즉시 실행)
  - 요약 2줄, 인사이트 1줄, 출처 최대 2개
- `P1`: 분석형(근거+영향 균형)
  - 요약 3줄, 인사이트 2줄, 출처 최대 3개
- `P2`: 스캔형(관찰 중심)
  - 요약 2줄, 인사이트 2줄, 출처 최대 2개

## 4-2) 멀티 주제 포맷

- `🧩 주제`: 카테고리 이모지 + 주제 제목을 다건(`n`)으로 출력
- `📌 핵심 요약`: 주제별 한 줄 요약(`n`)으로 출력
- `🔎 출처`: 주제별 링크 나열
- `🧠 Minerva 인사이트`: 상위 1~2개 분석 대상 + 카테고리 간 연관성 요약
- `👇 다음 액션` 블록은 기본 출력에서 제외

## 4-3) 언어 정책

- 텔레그램 브리핑은 한국어 우선
- `DEEPL_API_KEY`가 설정되어 있으면 Tier 정책에 따라 핵심 요약 중심 번역 수행
  - `P0`: `summary` + 상위 2개 `source snippet` 번역
  - `P1`: `summary` + 상위 1개 `source snippet` 번역
  - `P2`: 자동 번역 미적용(원문 유지)
- 제목(`title`)과 출처 제목은 원문 유지(번역 문자량 절감 목적)
- 번역 실패 또는 키 미설정 시 원문 유지(실패로 브리핑 중단하지 않음)
