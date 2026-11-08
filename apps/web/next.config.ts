import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output keeps the Docker image to the server and its traced deps.
  output: "standalone",
};

export default nextConfig;
