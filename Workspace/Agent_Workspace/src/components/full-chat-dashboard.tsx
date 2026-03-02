"use client";

import { CSSProperties, KeyboardEvent, useEffect, useRef, useState } from "react";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { AgentState, ChatMessage, createEmptyHistory } from "./chat-dashboard/types";
import { Orb } from "./chat-dashboard/orb";
import { motion, AnimatePresence } from "framer-motion";

export function FullChatDashboard() {
  const [activeAgent, setActiveAgent] = useState<CanonicalAgentId>("minerva");
  const [inputText, setInputText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [histories, setHistories] = useState(createEmptyHistory);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const currentAgent = AGENTS[activeAgent] ?? AGENTS.minerva;
  const activeHistory = histories[activeAgent];
  const { r, g, b, glow, secondary = glow } = currentAgent.color;

  // Derive global agent state
  let agentState: AgentState = "idle";
  if (isSending) {
    agentState = "thinking";
  } else if (isSpeaking) {
    agentState = "speaking";
  } else if (isFocused || inputText.length > 0) {
    agentState = "listening";
  }

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeHistory, isSending]);

  const handleSendText = async (rawMessage: string) => {
    const message = rawMessage.trim();
    if (!message || isSending) {
      return;
    }

    const targetAgent = activeAgent;
    const priorHistory = histories[targetAgent];
    const userMessage: ChatMessage = {
      role: "user",
      text: message,
      at: new Date().toISOString(),
    };

    setError(null);
    setIsSending(true);
    setInputText("");
    setHistories((prev) => ({
      ...prev,
      [targetAgent]: [...prev[targetAgent], userMessage],
    }));

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agentId: targetAgent,
          message,
          history: priorHistory,
        }),
      });

      if (!response.ok) {
        const failBody = await response.text();
        throw new Error(failBody || "요청 처리 실패");
      }

      const payload = await response.json();
      const assistantMessage: ChatMessage = {
        role: "assistant",
        text: payload.reply || payload.text || "응답이 없습니다.",
        at: new Date().toISOString(),
      };

      setHistories((prev) => ({
        ...prev,
        [targetAgent]: [...prev[targetAgent], assistantMessage],
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "알 수 없는 오류");
    } finally {
      setIsSending(false);
      // Trigger speaking state simulator
      setIsSpeaking(true);
      setTimeout(() => {
        setIsSpeaking(false);
      }, 2500); // 2.5s simulated speaking feedback
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSendText(inputText);
    }
  };

  const quickCommands = ["보안 점검", "상황 정리", "우선순위 요약", "트렌드 브리핑"];

  // Helper renderers for the Mini Tabs
  const renderMiniIcon = (id: CanonicalAgentId) => {
    switch (id) {
      case "minerva": return <polygon points="12,2 22,7 22,17 12,22 2,17 2,7" stroke="currentColor" strokeWidth="1.5" fill="none" />;
      case "clio": return <polygon points="12,2 22,12 12,22 2,12" stroke="currentColor" strokeWidth="1.5" fill="none" />;
      case "hermes": return <polygon points="12,3 22,21 2,21" stroke="currentColor" strokeWidth="1.5" fill="none" />;
      default: return null;
    }
  };

  const rootStyle: CSSProperties = {
    position: "relative",
    width: "100vw",
    height: "100vh",
    overflow: "hidden",
    background: "#050505",
    color: "#FAFAFA",
    fontFamily: "Space Grotesk, sans-serif",
  };
  const appBgStyle: CSSProperties = {
    position: "absolute",
    inset: 0,
    background: `radial-gradient(ellipse at 50% 40%, rgba(${r},${g},${b}, 0.15) 0%, transparent 60%)`,
    transition: "background 1s ease",
    zIndex: 0,
    pointerEvents: "none",
  };
  const selectorStyle: CSSProperties = {
    position: "absolute",
    top: "24px",
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: 50,
    display: "flex",
    gap: "12px",
  };
  const orbStageStyle: CSSProperties = {
    position: "absolute",
    top: 0,
    left: 0,
    width: "100%",
    height: "60vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 10,
  };
  const orbTransitionStyle: CSSProperties = { display: "flex", flexDirection: "column", alignItems: "center" };
  const orbShellStyle: CSSProperties = {
    width: "220px",
    height: "220px",
    borderRadius: "50%",
    boxShadow: `0 0 60px rgba(${r},${g},${b}, 0.25)`,
    transition: "all 0.5s ease",
  };
  const labelWrapStyle: CSSProperties = { textAlign: "center", marginTop: "32px" };
  const labelNameStyle: CSSProperties = { fontSize: "20px", fontWeight: 600, color: "#FAFAFA", letterSpacing: "1px" };
  const labelStatusStyle: CSSProperties = {
    fontSize: "14px",
    color: `rgba(${r},${g},${b}, 0.8)`,
    marginTop: "8px",
    height: "20px",
    transition: "color 0.3s ease",
  };
  const bottomPanelStyle: CSSProperties = {
    position: "fixed",
    bottom: 0,
    left: 0,
    width: "100%",
    height: "40vh",
    minHeight: "360px",
    display: "flex",
    flexDirection: "column",
    background: "linear-gradient(to bottom, rgba(5,5,5,0) 0%, rgba(5,5,5,0.85) 15%, rgba(5,5,5,1) 100%)",
    backdropFilter: "blur(16px)",
    borderTop: `1px solid rgba(${r},${g},${b}, 0.1)`,
    zIndex: 20,
  };
  const bottomContentStyle: CSSProperties = {
    width: "100%",
    maxWidth: "768px",
    margin: "0 auto",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    padding: "16px 24px 32px",
  };
  const scrollAreaStyle: CSSProperties = {
    flex: 1,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: "24px",
    paddingBottom: "24px",
    scrollbarWidth: "none",
  };
  const quickCommandsStyle: CSSProperties = {
    display: "flex",
    flexWrap: "wrap",
    gap: "12px",
    justifyContent: "center",
    marginTop: "auto",
  };
  const quickCommandButtonStyle: CSSProperties = {
    background: "rgba(255,255,255,0.05)",
    border: "1px solid rgba(255,255,255,0.1)",
    padding: "10px 18px",
    borderRadius: "20px",
    color: "rgba(255,255,255,0.8)",
    fontSize: "13px",
    cursor: "pointer",
    transition: "all 0.2s",
  };
  const sendingSpinnerStyle: CSSProperties = {
    width: "12px",
    height: "12px",
    borderRadius: "50%",
    border: `2px solid ${glow}`,
    borderRightColor: "transparent",
    animation: "spin 1s linear infinite",
  };
  const sendingBubbleStyle: CSSProperties = {
    alignSelf: "flex-start",
    background: "rgba(255,255,255,0.03)",
    padding: "12px 18px",
    borderRadius: "20px",
    borderBottomLeftRadius: "4px",
    color: `rgba(${r},${g},${b}, 0.7)`,
    fontSize: "13px",
    display: "flex",
    alignItems: "center",
    gap: "8px",
  };
  const errorBannerStyle: CSSProperties = {
    background: "rgba(239,68,68,0.1)",
    border: "1px solid rgba(239,68,68,0.3)",
    color: "#FCA5A5",
    padding: "10px",
    borderRadius: "8px",
    fontSize: "13px",
    marginBottom: "16px",
    textAlign: "center",
  };
  const composerStyle: CSSProperties = {
    position: "relative",
    background: "rgba(255,255,255,0.03)",
    border: `1px solid ${isFocused ? `rgba(${r},${g},${b}, 0.4)` : "rgba(255,255,255,0.08)"}`,
    borderRadius: "24px",
    transition: "all 0.3s ease",
    boxShadow: isFocused ? `0 0 20px rgba(${r},${g},${b}, 0.15)` : "none",
    display: "flex",
    alignItems: "flex-end",
    padding: "8px",
  };
  const composerInputStyle: CSSProperties = {
    flex: 1,
    background: "transparent",
    border: "none",
    color: "#FAFAFA",
    fontSize: "15px",
    padding: "10px 16px",
    outline: "none",
    resize: "none",
    minHeight: "44px",
    maxHeight: "120px",
    fontFamily: "inherit",
  };
  const sendButtonStyle: CSSProperties = {
    width: "40px",
    height: "40px",
    borderRadius: "50%",
    background: inputText.trim() && !isSending ? glow : "rgba(255,255,255,0.1)",
    border: "none",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: inputText.trim() && !isSending ? "#000" : "rgba(255,255,255,0.4)",
    cursor: inputText.trim() && !isSending ? "pointer" : "not-allowed",
    transition: "all 0.2s",
    marginLeft: "8px",
    flexShrink: 0,
  };

  return (
    <div style={rootStyle}>

      {/* 1. AppBackground */}
      <div
        className="app-bg"
        style={appBgStyle}
      />

      {/* 2. AgentSelector (Top Center) */}
      <div style={selectorStyle}>
        {(["minerva", "clio", "hermes"] as CanonicalAgentId[]).map((id) => {
          const isActive = id === activeAgent;
          const a = AGENTS[id];
          const selectorButtonStyle: CSSProperties = {
            width: "40px",
            height: "40px",
            borderRadius: "12px",
            background: isActive ? `rgba(${a.color.r},${a.color.g},${a.color.b}, 0.15)` : "rgba(255,255,255,0.03)",
            border: isActive ? `1px solid ${a.color.glow}` : "1px solid rgba(255,255,255,0.08)",
            boxShadow: isActive ? `0 0 15px rgba(${a.color.r},${a.color.g},${a.color.b}, 0.3)` : "none",
            color: isActive ? a.color.glow : "rgba(255,255,255,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
            transition: "all 0.3s ease",
          };
          return (
            <button
              key={id}
              onClick={() => setActiveAgent(id)}
              style={selectorButtonStyle}
              title={a.name}
            >
              <svg viewBox="0 0 24 24" width="20" height="20">
                {renderMiniIcon(id)}
              </svg>
            </button>
          );
        })}
      </div>

      {/* 3. OrbStage (Top 60%) */}
      <div style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "60vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", zIndex: 10 }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeAgent}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.1 }}
            transition={{ duration: 0.5, ease: "easeOut" }}
            style={{ display: "flex", flexDirection: "column", alignItems: "center" }}
          >
            {/* 3D Orb Canvas Wrapper */}
            <div style={{ width: "280px", height: "280px", position: "relative", filter: `drop-shadow(0 0 30px rgba(${r},${g},${b}, 0.2))`, transition: "all 0.5s ease" }}>
              <Orb colors={[glow, secondary]} agentState={agentState} />
            </div>

            {/* Agent Label & Status */}
            <div style={{ textAlign: "center", marginTop: "24px" }}>
              <div style={{ fontSize: "20px", fontWeight: 600, color: "#FAFAFA", letterSpacing: "1px" }}>{currentAgent.name}</div>
              <div style={{ fontSize: "14px", color: "rgba(255,255,255,0.4)", marginTop: "4px", height: "20px", transition: "color 0.3s ease" }}>
                {agentState === "idle" && "대기 중"}
                {agentState === "listening" && "듣고 있어요..."}
                {agentState === "thinking" && "생각하고 있어요..."}
                {agentState === "speaking" && "응답 중..."}
              </div>
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* 4. BottomChatPanel (Bottom 40%) */}
      <div style={bottomPanelStyle}>
        <div style={bottomContentStyle}>

          {/* Chat Scroll Area */}
          <div style={scrollAreaStyle}>

            {activeHistory.length === 0 ? (
              /* Quick Commands for Empty State */
              <div style={quickCommandsStyle}>
                {quickCommands.map((cmd, i) => (
                  <motion.button
                    key={cmd}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: i * 0.08 }}
                    onClick={() => handleSendText(cmd)}
                    style={quickCommandButtonStyle}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = `rgba(${r},${g},${b}, 0.15)`;
                      e.currentTarget.style.borderColor = glow;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = "rgba(255,255,255,0.05)";
                      e.currentTarget.style.borderColor = "rgba(255,255,255,0.1)";
                    }}
                  >
                    {cmd}
                  </motion.button>
                ))}
              </div>
            ) : (
              /* Message Bubbles */
              <>
                {activeHistory.map((msg, i) => {
                  const isUser = msg.role === "user";
                  const messageBubbleStyle: CSSProperties = {
                    alignSelf: isUser ? "flex-end" : "flex-start",
                    maxWidth: "85%",
                    background: isUser ? `rgba(${r},${g},${b}, 0.15)` : "rgba(255,255,255,0.05)",
                    border: isUser ? `1px solid rgba(${r},${g},${b}, 0.3)` : "1px solid rgba(255,255,255,0.08)",
                    padding: "14px 18px",
                    borderRadius: "20px",
                    borderBottomRightRadius: isUser ? "4px" : "20px",
                    borderBottomLeftRadius: isUser ? "20px" : "4px",
                    fontSize: "15px",
                    lineHeight: "1.6",
                    color: isUser ? "#FAFAFA" : "rgba(255,255,255,0.9)",
                    whiteSpace: "pre-wrap",
                  };
                  return (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      style={messageBubbleStyle}
                    >
                      {msg.text}
                    </motion.div>
                  );
                })}
                {isSending && (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    style={sendingBubbleStyle}
                  >
                    <div style={sendingSpinnerStyle} />
                    {currentAgent.name}가 생각 중...
                  </motion.div>
                )}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {/* Error Banner */}
          {error && (
            <div style={errorBannerStyle}>
              {error}
            </div>
          )}

          {/* Sticky Composer */}
          <div style={composerStyle}>
            <textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder={`${currentAgent.name}에게 메시지...`}
              rows={1}
              style={composerInputStyle}
            />
            <button
              onClick={() => handleSendText(inputText)}
              disabled={!inputText.trim() || isSending}
              style={sendButtonStyle}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}
