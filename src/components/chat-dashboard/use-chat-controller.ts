"use client";

import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { QUICK_COMMANDS } from "./quick-commands";
import { AgentState, ChatMessage, createEmptyHistory } from "./types";

type RuntimeMetricsSnapshot = {
  generatedAt: string;
  llm: {
    total: number;
    successRate: number;
    dailyLimit: number;
    remaining: number;
    quota429: number;
    fatalError: number;
  };
  orchestration: {
    totalEvents: number;
    pendingApprovals: number;
    telegram: {
      attempted: number;
      sent: number;
      successRate: number;
    };
  };
  deepl: {
    required: number;
    translated: number;
    failed: number;
    successRate: number;
  };
  security: {
    openIssues: number;
    securityIssues: number;
    warnings: number;
    latestReport: string | null;
  };
};

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
  runtimeMetrics: RuntimeMetricsSnapshot | null;
  runtimeMetricsError: string | null;
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
  const [isWorking, setIsWorking] = useState(false);
  const [runtimeMetrics, setRuntimeMetrics] = useState<RuntimeMetricsSnapshot | null>(null);
  const [runtimeMetricsError, setRuntimeMetricsError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const speakTimerRef = useRef<number | null>(null);

  const currentAgent = AGENTS[activeAgent] ?? AGENTS.minerva;
  const activeHistory = histories[activeAgent];
  const quickCommands = QUICK_COMMANDS[activeAgent];

  let agentState: AgentState = "idle";
  if (error) {
    agentState = "warning";
  } else if (isSending) {
    agentState = "thinking";
  } else if (isWorking) {
    agentState = "working";
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

  useEffect(() => {
    let mounted = true;
    let timer: number | null = null;

    const fetchMetrics = async () => {
      try {
        const response = await fetch("/api/runtime-metrics", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`runtime_metrics_http_${response.status}`);
        }
        const payload = (await response.json()) as {
          ok?: boolean;
          generatedAt?: string;
          llm?: {
            total?: number;
            successRate?: number;
            dailyLimit?: number;
            remaining?: number;
            quota429?: number;
            fatalError?: number;
          };
          orchestration?: {
            totalEvents?: number;
            pendingApprovals?: number;
            telegram?: { attempted?: number; sent?: number; successRate?: number };
          };
          deepl?: { required?: number; translated?: number; failed?: number; successRate?: number };
          security?: { openIssues?: number; securityIssues?: number; warnings?: number; latestReport?: string | null };
        };
        if (!mounted || payload.ok !== true || !payload.generatedAt) {
          return;
        }
        setRuntimeMetrics({
          generatedAt: payload.generatedAt,
          llm: {
            total: Number(payload.llm?.total ?? 0),
            successRate: Number(payload.llm?.successRate ?? 0),
            dailyLimit: Number(payload.llm?.dailyLimit ?? 0),
            remaining: Number(payload.llm?.remaining ?? 0),
            quota429: Number(payload.llm?.quota429 ?? 0),
            fatalError: Number(payload.llm?.fatalError ?? 0),
          },
          orchestration: {
            totalEvents: Number(payload.orchestration?.totalEvents ?? 0),
            pendingApprovals: Number(payload.orchestration?.pendingApprovals ?? 0),
            telegram: {
              attempted: Number(payload.orchestration?.telegram?.attempted ?? 0),
              sent: Number(payload.orchestration?.telegram?.sent ?? 0),
              successRate: Number(payload.orchestration?.telegram?.successRate ?? 0),
            },
          },
          deepl: {
            required: Number(payload.deepl?.required ?? 0),
            translated: Number(payload.deepl?.translated ?? 0),
            failed: Number(payload.deepl?.failed ?? 0),
            successRate: Number(payload.deepl?.successRate ?? 0),
          },
          security: {
            openIssues: Number(payload.security?.openIssues ?? 0),
            securityIssues: Number(payload.security?.securityIssues ?? 0),
            warnings: Number(payload.security?.warnings ?? 0),
            latestReport: typeof payload.security?.latestReport === "string" ? payload.security.latestReport : null,
          },
        });
        setRuntimeMetricsError(null);
      } catch (caught) {
        if (!mounted) {
          return;
        }
        setRuntimeMetricsError(caught instanceof Error ? caught.message : "runtime_metrics_fetch_failed");
      }
    };

    void fetchMetrics();
    timer = window.setInterval(() => {
      void fetchMetrics();
    }, 30000);

    return () => {
      mounted = false;
      if (timer !== null) {
        window.clearInterval(timer);
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

    let requestSucceeded = false;

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
      requestSucceeded = true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "알 수 없는 오류");
    } finally {
      setIsSending(false);
      if (speakTimerRef.current !== null) {
        window.clearTimeout(speakTimerRef.current);
      }
      if (requestSucceeded) {
        setIsWorking(true);
        speakTimerRef.current = window.setTimeout(() => {
          setIsWorking(false);
        }, 2200);
      } else {
        setIsWorking(false);
      }
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
    runtimeMetrics,
    runtimeMetricsError,
    messagesEndRef,
    handleSendText,
    handleKeyDown,
  };
}
