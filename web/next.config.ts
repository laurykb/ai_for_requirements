import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  // Produit un serveur Node autonome pour les installations sans npm/Internet.
  output: "standalone",
  async rewrites() {
    return [{
      source: "/backend/:path*",
      // Local development runs the API on the host. Docker Compose overrides
      // INTERNAL_API_BASE with http://api:8000 inside its private network.
      destination: `${process.env.INTERNAL_API_BASE ?? "http://127.0.0.1:8000"}/:path*`,
    }];
  },
};

export default nextConfig;
