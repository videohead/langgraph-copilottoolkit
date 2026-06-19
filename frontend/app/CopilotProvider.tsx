"use client";

import { ReactNode } from "react";
import { CopilotKit } from "@copilotkit/react-core";

type CopilotProviderProps = {
  children: ReactNode;
};

export default function CopilotProvider({ children }: CopilotProviderProps) {
  return <CopilotKit runtimeUrl="/api/copilotkit">{children}</CopilotKit>;
}