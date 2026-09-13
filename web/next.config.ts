import type { NextConfig } from "next";
import path from "path";
import { fileURLToPath } from "url";

/** Project Pages URL is https://<user>.github.io/electionmodel26/ */
const repo = "electionmodel26";
const isGithubPages = process.env.GITHUB_PAGES === "true";
const webRoot = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath: isGithubPages ? `/${repo}` : "",
  assetPrefix: isGithubPages ? `/${repo}/` : undefined,
  env: {
    NEXT_PUBLIC_BASE_PATH: isGithubPages ? `/${repo}` : "",
  },
  // Parent repo has an empty package-lock.json that confuses Turbopack root inference.
  turbopack: {
    root: webRoot,
  },
  // Prevent Next from writing AGENTS.md into the app on each boot.
  agentRules: false,
};

export default nextConfig;
