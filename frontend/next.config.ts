import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow the dev server to be proxied by Lando / nginx
  output: "standalone",
  experimental: {
    // Needed for streaming responses in Next.js API routes
    serverActions: { bodySizeLimit: "2mb" },
  },
};

export default nextConfig;
