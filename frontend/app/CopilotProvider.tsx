"use client";

import { ReactNode } from "react";
import { CopilotKit } from "@copilotkit/react-core";

type CopilotProviderProps = {
  children: ReactNode;
};

function isExpectedTerminationError(error: unknown): boolean {
  if (!error || typeof error !== "object") {
    return false;
  }

  const maybeError = error as {
    code?: unknown;
    message?: unknown;
    context?: { message?: unknown };
  };

  const code = typeof maybeError.code === "string" ? maybeError.code.toLowerCase() : "";
  const message =
    typeof maybeError.message === "string"
      ? maybeError.message.toLowerCase()
      : typeof maybeError.context?.message === "string"
        ? maybeError.context.message.toLowerCase()
        : "";

  const rawText = JSON.stringify(error).toLowerCase();
  const hasExpectedAbortText =
    message === "terminated" ||
    message.includes("cancel") ||
    message.includes("abort") ||
    rawText.includes("terminated") ||
    rawText.includes("cancel") ||
    rawText.includes("abort");

  return code === "agent_run_error_event" && hasExpectedAbortText;
}

export default function CopilotProvider({ children }: CopilotProviderProps) {
  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      onError={(error) => {
        if (isExpectedTerminationError(error)) {
          return;
        }
        console.error("[CopilotKit] Agent error:", error);
      }}
    >
      {children}
    </CopilotKit>
  );
}
