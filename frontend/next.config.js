/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        // Replaced 'localhost' with 'app' (the Docker service name)
        destination: 'http://app:8000/api/:path*', 
      },
    ];
  },
};

module.exports = nextConfig;