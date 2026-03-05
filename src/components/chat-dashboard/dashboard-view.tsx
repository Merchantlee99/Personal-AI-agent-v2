"use client";

import { AnimatePresence, motion } from "framer-motion";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { Orb } from "./orb";
import { ChatDashboardController } from "./use-chat-controller";
import styles from "./dashboard-view.module.css";

type DashboardViewProps = {
  controller: ChatDashboardController;
};

type AgentSwitchId = CanonicalAgentId | "aegis";
type SignalTone = "normal" | "active" | "warning";

const ROOT_AGENT_CLASS: Record<CanonicalAgentId, string> = {
  minerva: styles.agentMinerva,
  clio: styles.agentClio,
  hermes: styles.agentHermes,
};

const BUTTON_AGENT_CLASS: Record<CanonicalAgentId, string> = {
  minerva: styles.agentButtonMinerva,
  clio: styles.agentButtonClio,
  hermes: styles.agentButtonHermes,
};

const AGENT_SWITCHES: Array<{ id: AgentSwitchId; title: string; enabled: boolean }> = [
  { id: "minerva", title: AGENTS.minerva.name, enabled: true },
  { id: "clio", title: AGENTS.clio.name, enabled: true },
  { id: "hermes", title: AGENTS.hermes.name, enabled: true },
  { id: "aegis", title: "Aegis (준비중)", enabled: false },
];

function renderMiniIcon(id: AgentSwitchId) {
  switch (id) {
    case "minerva":
      return <polygon points="12,2 22,7 22,17 12,22 2,17 2,7" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    case "clio":
      return <polygon points="12,2 22,12 12,22 2,12" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    case "hermes":
      return <polygon points="12,3 22,21 2,21" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    case "aegis":
      return (
        <path
          d="M12 2L20 6V12C20 17 16.9 20.9 12 22C7.1 20.9 4 17 4 12V6L12 2Z"
          stroke="currentColor"
          strokeWidth="1.5"
          fill="none"
        />
      );
    default:
      return null;
  }
}

function getStateText(state: ChatDashboardController["agentState"]): string {
  if (state === "idle") return "대기 중";
  if (state === "listening") return "입력 감지 중";
  if (state === "thinking") return "사고 연산 중";
  if (state === "working") return "작동 중";
  return "경고 모드";
}

function getStateTone(state: ChatDashboardController["agentState"]): SignalTone {
  if (state === "warning") return "warning";
  if (state === "thinking" || state === "working") return "active";
  return "normal";
}

function signalToneClass(tone: SignalTone): string {
  if (tone === "active") return styles.signalChipActive;
  if (tone === "warning") return styles.signalChipWarning;
  return "";
}

export function DashboardView({ controller }: DashboardViewProps) {
  const {
    activeAgent,
    setActiveAgent,
    inputText,
    setInputText,
    isSending,
    isFocused,
    setIsFocused,
    error,
    activeHistory,
    currentAgent,
    agentState,
    quickCommands,
    messagesEndRef,
    handleSendText,
    handleKeyDown,
  } = controller;

  const rootClass = `${styles.root} ${ROOT_AGENT_CLASS[activeAgent]} ${
    agentState === "warning" ? styles.rootWarning : ""
  }`;
  const composerClass = `${styles.composer} ${isFocused ? styles.composerFocused : ""}`;
  const sendButtonClass = `${styles.sendButton} ${inputText.trim() && !isSending ? styles.sendButtonEnabled : ""}`;
  const stateTone = getStateTone(agentState);
  const stateToneClass = signalToneClass(stateTone);

  const signals: Array<{ label: string; value: string; tone: SignalTone }> = [
    { label: "Core", value: getStateText(agentState), tone: stateTone },
    {
      label: "Guard",
      value: agentState === "warning" ? "격리 필요" : "정상",
      tone: agentState === "warning" ? "warning" : "normal",
    },
    {
      label: "Action",
      value: isSending ? "요청 처리중" : "준비 완료",
      tone: isSending ? "active" : "normal",
    },
  ];

  return (
    <div className={rootClass}>
      <div className={styles.appBg} />
      <div className={styles.scanGrid} />

      <div className={styles.selector}>
        {AGENT_SWITCHES.map((item) => {
          const isRealAgent = item.id !== "aegis";
          const isActive = isRealAgent ? item.id === activeAgent : false;
          const realAgentId = (isRealAgent ? item.id : "minerva") as CanonicalAgentId;
          const toneClass = isRealAgent ? BUTTON_AGENT_CLASS[realAgentId] : styles.agentButtonAegis;
          const buttonClass = `${styles.agentButton} ${toneClass} ${isActive ? styles.agentButtonActive : ""} ${
            item.enabled ? "" : styles.agentButtonDisabled
          }`;

          return (
            <button
              key={item.id}
              onClick={() => (isRealAgent ? setActiveAgent(realAgentId) : undefined)}
              className={buttonClass}
              title={item.title}
              disabled={!item.enabled}
            >
              <svg viewBox="0 0 24 24" width="20" height="20">
                {renderMiniIcon(item.id)}
              </svg>
            </button>
          );
        })}
      </div>

      <div className={styles.orbStage}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeAgent}
            initial={{ opacity: 0, scale: 0.82 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.08 }}
            transition={{ duration: 0.45, ease: "easeOut" }}
            className={styles.orbTransition}
          >
            <div className={styles.orbShell}>
              <Orb colors={[currentAgent.color.glow, currentAgent.color.secondary]} agentState={agentState} />
            </div>

            <div className={styles.agentLabelWrap}>
              <div className={styles.agentName}>{currentAgent.name}</div>
              <div className={`${styles.statusBadge} ${stateToneClass}`}>
                <span className={styles.statusDot} />
                {getStateText(agentState)}
              </div>
            </div>

            <div className={styles.signalRail}>
              {signals.map((item) => (
                <div key={item.label} className={`${styles.signalChip} ${signalToneClass(item.tone)}`}>
                  <span className={styles.signalLabel}>{item.label}</span>
                  <span className={styles.signalValue}>{item.value}</span>
                </div>
              ))}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className={styles.bottomPanel}>
        <div className={styles.bottomContent}>
          <div className={styles.scrollArea}>
            {activeHistory.length === 0 ? (
              <div className={styles.quickCommands}>
                {quickCommands.map((item, index) => (
                  <motion.button
                    key={`${item.label}-${index}`}
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: index * 0.08 }}
                    onClick={() => void handleSendText(item.message)}
                    className={styles.quickCommand}
                  >
                    {item.label}
                  </motion.button>
                ))}
              </div>
            ) : (
              <>
                {activeHistory.map((message, index) => {
                  const bubbleClass = `${styles.bubble} ${
                    message.role === "user" ? styles.bubbleUser : styles.bubbleAssistant
                  }`;
                  return (
                    <motion.div
                      key={`${message.at}-${index}`}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={bubbleClass}
                    >
                      {message.text}
                    </motion.div>
                  );
                })}

                {isSending ? (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={styles.sendingBubble}>
                    <span className={styles.spinner} />
                    {currentAgent.name}가 사고를 정리하는 중...
                  </motion.div>
                ) : null}

                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {error ? <div className={styles.errorBanner}>{error}</div> : null}

          <div className={composerClass}>
            <textarea
              value={inputText}
              onChange={(event) => setInputText(event.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              placeholder={`${currentAgent.name}에게 메시지...`}
              rows={1}
              className={styles.composerInput}
            />
            <button
              onClick={() => void handleSendText(inputText)}
              disabled={!inputText.trim() || isSending}
              className={sendButtonClass}
            >
              <svg
                className={styles.sendIcon}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
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
