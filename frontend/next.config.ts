import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactCompiler: true,
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_URL || 'http://api-gateway:8000'}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;