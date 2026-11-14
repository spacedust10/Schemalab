import type { NextConfig } from "next";
import { contentSecurityPolicy } from "./src/lib/csp";

const nextConfig: NextConfig = {
  // Standalone output keeps the Docker image to the server and its traced deps.
  output: "standalone",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy(process.env.NODE_ENV === "development") },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
      {
        // The DuckDB-WASM version is in the path, so a file there never changes.
        source: "/duckdb/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
};

export default nextConfig;
