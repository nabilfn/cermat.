import type { NextConfig } from "next";

// The browser only ever talks to this origin; /api/* is proxied to FastAPI.
// Keeps the session cookie first-party (no third-party cookies between web and
// API domains) and removes hard-coded API hosts from the client bundle.
const apiOrigin = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
const maxUploadMb = Number(process.env.MAX_UPLOAD_MB ?? 15);

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  devIndicators: false,
  experimental: {
    // FastAPI enforces the real limit; the proxy just must not truncate first.
    proxyClientMaxBodySize: `${maxUploadMb + 1}mb`,
    // AI extraction can take a while; the API applies its own model timeout.
    proxyTimeout: 180_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
