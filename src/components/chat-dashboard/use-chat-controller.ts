"use client";

import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { QUICK_COMMANDS } from "./quick-commands";
import { AgentState, ChatMessage, createEmptyHistory } from "./types";

export type ChatDashboardController = {
  activeAgent: CanonicalAgentId;
  setActiveAgent: (value: CanonicalAgentId) => void;
  inputText: string;
  setInputText: (value: string) => void;
  isSending: boolean;
  isFocused: boolean;
  setIsFocused: (value: boolean) => void;
  error: string | null;
  activeHistory: ChatMessage[];
  currentAgent: (typeof AGENTS)[CanonicalAgentId];
  agentState: AgentState;
  quickCommands: { label: string; message: string }[];
  messagesEndRef: React.RefObject<HTMLDivElement | null>;
  handleSendText: (rawMessage: string) => Promise<void>;
  handleKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
};

export function useChatController(): ChatDashboardController {
  const [activeAgent, setActiveAgent] = useState<CanonicalAgentId>("minerva");
  const [inputText, setInputText] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [histories, setHistories] = useState(createEmptyHistory);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const speakTimerRef = useRef<number | null>(null);

  const currentAgent = AGENTS[activeAgent] ?? AGENTS.minerva;
  const activeHistory = histories[activeAgent];
  const quickCommands = QUICK_COMMANDS[activeAgent];

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

  useEffect(() => {
    return () => {
      if (speakTimerRef.current !== null) {
        window.clearTimeout(speakTimerRef.current);
      }
    };
  }, []);

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
      setIsSpeaking(true);
      if (speakTimerRef.current !== null) {
        window.clearTimeout(speakTimerRef.current);
      }
      speakTimerRef.current = window.setTimeout(() => {
        setIsSpeaking(false);
      }, 2500);
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSendText(inputText);
    }
  };

  return {
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
  };
}
