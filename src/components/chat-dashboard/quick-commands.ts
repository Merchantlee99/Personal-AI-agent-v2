import type { CanonicalAgentId } from "@/lib/agents";

export const QUICK_COMMANDS: Record<CanonicalAgentId, { label: string; message: string }[]> = {
  minerva: [
    { label: "조언 요청", message: "현재 상황에 대해 조언해줘" },
    { label: "보안 점검", message: "보안 점검해줘" },
    { label: "상황 정리", message: "내 현재 프로젝트 상황을 정리해줘" },
    { label: "우선순위 정리", message: "지금 우선순위를 정해줘" },
  ],
  clio: [
    { label: "노트 정리", message: "최근 자료를 옵시디언에 정리해줘" },
    { label: "연결 분석", message: "관련 노트를 찾아서 연결해줘" },
    { label: "NotebookLM 가공", message: "NotebookLM용으로 자료를 가공해줘" },
    { label: "중복 확인", message: "중복 노트가 있는지 확인해줘" },
  ],
  hermes: [
    { label: "핫 트렌드", message: "오늘의 핫 트렌드를 조사해줘" },
    { label: "경쟁사 동향", message: "TripPixel 경쟁사 동향을 확인해줘" },
    { label: "시장 분석", message: "관광 스타트업 시장을 분석해줘" },
    { label: "기술 트렌드", message: "AI 에이전트 기술 트렌드를 조사해줘" },
  ],
};
