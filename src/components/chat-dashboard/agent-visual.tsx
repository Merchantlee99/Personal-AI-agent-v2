import type { CanonicalAgentId } from "@/lib/agents";
import { AGENTS } from "@/lib/agents";
import type { DashboardTheme } from "./theme";
import type { AgentState } from "./types";
import { MiniShape } from "./mini-shape";

type AgentVisualProps = {
    agentId: CanonicalAgentId;
    state: AgentState;
    theme: DashboardTheme;
    size?: "mini" | "lg";
};

export function AgentVisual({ agentId, state, theme, size = "lg" }: AgentVisualProps) {
    const agent = AGENTS[agentId];
    const { r, g, b, glow } = agent.color;
    const isLight = theme === 'light';
    const glowMult = isLight ? 0.4 : 1;

    if (size === "mini") {
        // Mini 버전 유지 (Header / Input Area)
        return (
            <div
                style={{
                    width: "24px",
                    height: "24px",
                    borderRadius: "6px",
                    background: `rgba(${r},${g},${b}, ${isLight ? 0.1 : 0.15})`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    position: "relative",
                    // @ts-ignore - CSS custom properties
                    "--agent-color-30": `rgba(${r},${g},${b}, ${0.3 * glowMult})`,
                    "--agent-color-60": `rgba(${r},${g},${b}, ${0.6 * glowMult})`,
                }}
            >
                <MiniShape id={agentId} strokeColor={glow} size={12} />

                {state === "thinking" && (
                    <div
                        style={{
                            position: "absolute",
                            inset: "-3px",
                            borderRadius: "8px",
                            border: "2px solid transparent",
                            borderTopColor: glow,
                            animation: "mini-spin 1s linear infinite",
                        }}
                    />
                )}

                {state === "speaking" && (
                    <div
                        style={{
                            position: "absolute",
                            inset: "-2px",
                            borderRadius: "8px",
                            boxShadow: `0 0 8px ${glow}`,
                            animation: "mini-glow 0.5s ease-in-out infinite alternate",
                        }}
                    />
                )}
            </div>
        );
    }

    // Large 버전 - 새로 제공된 JARVIS 홀로그램 사양 적용
    const params = {
        idle: { coreScale: 1, coreBright: 0.6, pulseSpeed: 4, ringSpeed: 40, shapeRotate: false, ripple: false, energySpeed: 8 },
        listening: { coreScale: 1.1, coreBright: 0.8, pulseSpeed: 2.5, ringSpeed: 30, shapeRotate: false, ripple: false, energySpeed: 5 },
        thinking: { coreScale: 1.3, coreBright: 1, pulseSpeed: 0.8, ringSpeed: 6, shapeRotate: true, ripple: true, energySpeed: 2 },
        speaking: { coreScale: 1.15, coreBright: 0.9, pulseSpeed: 0.3, ringSpeed: 20, shapeRotate: false, ripple: true, energySpeed: 4 },
    }[state];

    return (
        <div style={{
            position: 'relative',
            width: '280px',
            height: '280px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
        }}>

            {/* ========== 배경 방사형 글로우 ========== */}
            <div style={{
                position: 'absolute',
                width: '400px',
                height: '400px',
                borderRadius: '50%',
                background: `radial-gradient(circle,
          rgba(${r},${g},${b}, ${0.20 * glowMult}) 0%,
          rgba(${r},${g},${b}, ${0.08 * glowMult}) 30%,
          rgba(${r},${g},${b}, ${0.02 * glowMult}) 50%,
          transparent 70%
        )`,
                animation: `bg-pulse ${params.pulseSpeed * 1.5}s ease-in-out infinite`,
                pointerEvents: 'none',
            }} />

            {/* ========== 파동 레이어 (THINKING/SPEAKING에서만) ========== */}
            {params.ripple && [0, 1, 2].map((i) => (
                <div key={`ripple-${i}`} style={{
                    position: 'absolute',
                    width: '100%',
                    height: '100%',
                    borderRadius: '50%',
                    border: `1.5px solid rgba(${r},${g},${b}, ${0.4 * glowMult})`,
                    animation: `ripple-expand 2.5s ease-out ${i * 0.8}s infinite`,
                    pointerEvents: 'none',
                }} />
            ))}

            {/* ========== 외부 링 1 — 260px, dashed, 회전 ========== */}
            <div style={{
                position: 'absolute',
                width: '260px',
                height: '260px',
                borderRadius: '50%',
                border: `1px dashed rgba(${r},${g},${b}, ${0.25 * glowMult})`,
                animation: `spin ${params.ringSpeed}s linear infinite`,
                boxShadow: `0 0 15px rgba(${r},${g},${b}, ${0.08 * glowMult})`,
            }} />

            {/* ========== 외부 링 2 — 220px, solid, 반대 회전 ========== */}
            <div style={{
                position: 'absolute',
                width: '220px',
                height: '220px',
                borderRadius: '50%',
                border: `1px solid rgba(${r},${g},${b}, ${0.15 * glowMult})`,
                animation: `spin-reverse ${params.ringSpeed * 1.5}s linear infinite`,
                boxShadow: `0 0 10px rgba(${r},${g},${b}, ${0.05 * glowMult})`,
            }} />

            {/* ========== 중간 링 — 170px, 호흡 ========== */}
            <div style={{
                position: 'absolute',
                width: '170px',
                height: '170px',
                borderRadius: '50%',
                border: `1px solid rgba(${r},${g},${b}, ${0.20 * glowMult})`,
                animation: `ring-breathe 3s ease-in-out infinite`,
                boxShadow: isLight ? `0 0 10px rgba(${r},${g},${b}, 0.10)` : `
          0 0 20px rgba(${r},${g},${b}, 0.10),
          inset 0 0 20px rgba(${r},${g},${b}, 0.05)
        `,
            }} />

            {/* ========== 내부 링 — 120px, 글로우 강한 호흡 ========== */}
            <div style={{
                position: 'absolute',
                width: '120px',
                height: '120px',
                borderRadius: '50%',
                border: `1.5px solid rgba(${r},${g},${b}, ${0.35 * glowMult})`,
                animation: `ring-breathe 2.5s ease-in-out 0.5s infinite`,
                boxShadow: isLight ? `0 0 15px rgba(${r},${g},${b}, 0.15)` : `
          0 0 25px rgba(${r},${g},${b}, 0.15),
          inset 0 0 25px rgba(${r},${g},${b}, 0.08)
        `,
            }} />

            {/* ========== 기하학적 도형 (3겹 SVG) ========== */}
            <svg viewBox="0 0 100 100" style={{
                position: 'absolute',
                width: '100px',
                height: '100px',
                animation: params.shapeRotate
                    ? `spin 8s linear infinite`
                    : 'none',
                filter: `drop-shadow(0 0 ${isLight ? 10 : 20}px rgba(${r},${g},${b}, ${0.5 * glowMult}))`,
                transition: 'filter 0.5s ease',
            }}>

                {/* ===== 미네르바: 육각형 3겹 ===== */}
                {agent.id === 'minerva' && <>
                    {/* 외부 — 흰 선, 글로우 */}
                    <polygon points="50,8 90,29 90,71 50,92 10,71 10,29"
                        stroke={`rgba(255,255,255,${0.5 * glowMult})`} strokeWidth="1.2" fill="none" />
                    {/* 중간 — 반투명 */}
                    <polygon points="50,18 80,34 80,66 50,82 20,66 20,34"
                        stroke={`rgba(255,255,255,${0.3 * glowMult})`} strokeWidth="0.8" fill="none" />
                    {/* 내부 — fill 있음 */}
                    <polygon points="50,28 70,39 70,61 50,72 30,61 30,39"
                        stroke={`rgba(255,255,255,${0.6 * glowMult})`} strokeWidth="0.8"
                        fill={`rgba(${r},${g},${b}, ${0.12 * glowMult})`} />
                    {/* 에너지 흐름 오버레이 */}
                    <polygon points="50,8 90,29 90,71 50,92 10,71 10,29"
                        stroke={glow} strokeWidth="2" fill="none"
                        strokeDasharray="25 175" opacity={0.8 * glowMult}
                        style={{ animation: `energy-flow ${params.energySpeed}s linear infinite` }} />
                    {/* 꼭짓점→중심 연결선 */}
                    {[[50, 8], [90, 29], [90, 71], [50, 92], [10, 71], [10, 29]].map(([x, y], i) => (
                        <line key={i} x1={x} y1={y} x2="50" y2="50"
                            stroke={`rgba(${r},${g},${b}, ${0.15 * glowMult})`} strokeWidth="0.5"
                            style={{ animation: `line-flicker 3s ease-in-out ${i * 0.5}s infinite alternate` }} />
                    ))}
                </>}

                {/* ===== 클리오: 마름모 3겹 ===== */}
                {agent.id === 'clio' && <>
                    <polygon points="50,5 95,50 50,95 5,50"
                        stroke={`rgba(255,255,255,${0.5 * glowMult})`} strokeWidth="1.2" fill="none" />
                    <polygon points="50,18 82,50 50,82 18,50"
                        stroke={`rgba(255,255,255,${0.3 * glowMult})`} strokeWidth="0.8" fill="none" />
                    <polygon points="50,30 70,50 50,70 30,50"
                        stroke={`rgba(255,255,255,${0.6 * glowMult})`} strokeWidth="0.8"
                        fill={`rgba(${r},${g},${b}, ${0.12 * glowMult})`} />
                    <polygon points="50,5 95,50 50,95 5,50"
                        stroke={glow} strokeWidth="2" fill="none"
                        strokeDasharray="20 160" opacity={0.8 * glowMult}
                        style={{ animation: `energy-flow ${params.energySpeed}s linear infinite` }} />
                    {[[50, 5], [95, 50], [50, 95], [5, 50]].map(([x, y], i) => (
                        <line key={i} x1={x} y1={y} x2="50" y2="50"
                            stroke={`rgba(${r},${g},${b}, ${0.15 * glowMult})`} strokeWidth="0.5"
                            style={{ animation: `line-flicker 3s ease-in-out ${i * 0.7}s infinite alternate` }} />
                    ))}
                </>}

                {/* ===== 헤르메스: 삼각형 3겹 ===== */}
                {agent.id === 'hermes' && <>
                    <polygon points="50,5 97,88 3,88"
                        stroke={`rgba(255,255,255,${0.5 * glowMult})`} strokeWidth="1.2" fill="none" />
                    <polygon points="50,20 83,80 17,80"
                        stroke={`rgba(255,255,255,${0.3 * glowMult})`} strokeWidth="0.8" fill="none" />
                    <polygon points="50,33 70,72 30,72"
                        stroke={`rgba(255,255,255,${0.6 * glowMult})`} strokeWidth="0.8"
                        fill={`rgba(${r},${g},${b}, ${0.12 * glowMult})`} />
                    <polygon points="50,5 97,88 3,88"
                        stroke={glow} strokeWidth="2" fill="none"
                        strokeDasharray="18 140" opacity={0.8 * glowMult}
                        style={{ animation: `energy-flow ${params.energySpeed}s linear infinite` }} />
                    {[[50, 5], [97, 88], [3, 88]].map(([x, y], i) => (
                        <line key={i} x1={x} y1={y} x2="50" y2="50"
                            stroke={`rgba(${r},${g},${b}, ${0.15 * glowMult})`} strokeWidth="0.5"
                            style={{ animation: `line-flicker 3s ease-in-out ${i * 0.9}s infinite alternate` }} />
                    ))}
                </>}
            </svg>

            {/* ========== 핵 외곽 글로우 (큰 빛번짐) ========== */}
            <div style={{
                position: 'absolute',
                width: '60px',
                height: '60px',
                borderRadius: '50%',
                background: `radial-gradient(circle,
          rgba(255,255,255, ${0.25 * glowMult}) 0%,
          rgba(${r},${g},${b}, ${0.35 * glowMult}) 40%,
          transparent 70%
        )`,
                filter: `blur(${isLight ? 8 : 12}px)`,
                animation: `core-glow-pulse ${params.pulseSpeed}s ease-in-out infinite`,
                transform: `scale(${params.coreScale})`,
                transition: 'transform 0.5s ease',
            }} />

            {/* ========== 핵 중간 글로우 (선명한 빛) ========== */}
            <div style={{
                position: 'absolute',
                width: '35px',
                height: '35px',
                borderRadius: '50%',
                background: `radial-gradient(circle,
          rgba(255,255,255, ${0.5 * glowMult}) 0%,
          rgba(${r},${g},${b}, ${0.5 * glowMult}) 50%,
          transparent 100%
        )`,
                filter: `blur(${isLight ? 4 : 6}px)`,
                animation: `core-mid-pulse ${params.pulseSpeed * 0.8}s ease-in-out infinite`,
                transform: `scale(${params.coreScale})`,
                transition: 'transform 0.5s ease',
            }} />

            {/* ========== 핵 플레어 (불규칙 빛 폭발) ========== */}
            <div style={{
                position: 'absolute',
                width: '20px',
                height: '20px',
                borderRadius: '50%',
                background: `radial-gradient(circle,
          rgba(255,255,255, ${0.6 * glowMult}) 0%,
          rgba(${r},${g},${b}, ${0.3 * glowMult}) 100%
        )`,
                filter: 'blur(4px)',
                animation: `flare ${params.pulseSpeed * 0.7}s ease-in-out infinite alternate`,
            }} />

            {/* ========== 핵 코어 (가장 밝고 날카로운 점) ========== */}
            <div style={{
                position: 'absolute',
                width: '10px',
                height: '10px',
                borderRadius: '50%',
                background: `radial-gradient(circle,
          rgba(255,255,255, ${1 * glowMult}) 0%,
          rgba(255,255,255, ${0.9 * glowMult}) 30%,
          rgba(${r},${g},${b}, ${0.8 * glowMult}) 60%,
          transparent 100%
        )`,
                boxShadow: isLight ? `
          0 0 4px rgba(255,255,255, 0.9),
          0 0 10px rgba(${r},${g},${b}, 0.6)
        ` : `
          0 0 8px rgba(255,255,255, 0.9),
          0 0 20px rgba(255,255,255, 0.5),
          0 0 40px rgba(${r},${g},${b}, 0.6),
          0 0 80px rgba(${r},${g},${b}, 0.3)
        `,
                animation: `core-intense ${params.pulseSpeed}s ease-in-out infinite`,
                transform: `scale(${params.coreScale})`,
                transition: 'transform 0.5s ease',
            }} />

        </div>
    );
}
