/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false, // Disable double-rendering in dev mode for maximum UI responsiveness
  swcMinify: true,
  experimental: {
    optimizePackageImports: ['lucide-react', 'recharts', 'date-fns'],
  },
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: (process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000') + '/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;