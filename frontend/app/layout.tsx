import type { Metadata } from "next";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";
import CopilotProvider from "./CopilotProvider";

export const metadata: Metadata = {
  title: "LangGraph AI",
  description: "CopilotKit + LangGraph + Ollama",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <CopilotProvider>{children}</CopilotProvider>
      </body>
    </html>
  );
}
