"use client";

import { AnimatePresence, motion } from "framer-motion";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { Orb } from "./orb";
import { ChatDashboardController } from "./use-chat-controller";
import styles from "./dashboard-view.module.css";

type DashboardViewProps = {
  controller: ChatDashboardController;
};

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

function renderMiniIcon(id: CanonicalAgentId) {
  switch (id) {
    case "minerva":
      return <polygon points="12,2 22,7 22,17 12,22 2,17 2,7" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    case "clio":
      return <polygon points="12,2 22,12 12,22 2,12" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    case "hermes":
      return <polygon points="12,3 22,21 2,21" stroke="currentColor" strokeWidth="1.5" fill="none" />;
    default:
      return null;
  }
}

function getStateText(state: ChatDashboardController["agentState"]): string {
  if (state === "idle") {
    return "대기 중";
  }
  if (state === "listening") {
    return "듣고 있어요...";
  }
  if (state === "thinking") {
    return "생각하고 있어요...";
  }
  return "응답 중...";
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

  const rootClass = `${styles.root} ${ROOT_AGENT_CLASS[activeAgent]}`;
  const composerClass = `${styles.composer} ${isFocused ? styles.composerFocused : ""}`;
  const sendButtonClass = `${styles.sendButton} ${inputText.trim() && !isSending ? styles.sendButtonEnabled : ""}`;

  return (
    <div className={rootClass}>
      <div className={styles.appBg} />

      <div className={styles.selector}>
        {(["minerva", "clio", "hermes"] as CanonicalAgentId[]).map((id) => {
          const isActive = id === activeAgent;
          const buttonClass = `${styles.agentButton} ${BUTTON_AGENT_CLASS[id]} ${isActive ? styles.agentButtonActive : ""}`;
          return (
            <button key={id} onClick={() => setActiveAgent(id)} className={buttonClass} title={AGENTS[id].name}>
              <svg viewBox="0 0 24 24" width="20" height="20">
                {renderMiniIcon(id)}
              </svg>
            </button>
          );
        })}
      </div>

      <div className={styles.orbStage}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeAgent}
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.1 }}
            transition={{ duration: 0.5, ease: "easeOut" }}
            className={styles.orbTransition}
          >
            <div className={styles.orbShell}>
              <Orb colors={[currentAgent.color.glow, currentAgent.color.secondary]} agentState={agentState} />
            </div>
            <div className={styles.agentLabelWrap}>
              <div className={styles.agentName}>{currentAgent.name}</div>
              <div className={styles.agentStatus}>{getStateText(agentState)}</div>
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
                    <motion.div key={`${message.at}-${index}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className={bubbleClass}>
                      {message.text}
                    </motion.div>
                  );
                })}

                {isSending ? (
                  <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className={styles.sendingBubble}>
                    <span className={styles.spinner} />
                    {currentAgent.name}가 생각 중...
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
            <button onClick={() => void handleSendText(inputText)} disabled={!inputText.trim() || isSending} className={sendButtonClass}>
              <svg className={styles.sendIcon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
