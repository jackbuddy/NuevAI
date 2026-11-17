import type { NextConfig } from 'next';

// The old Vercel address forwards to the real domain. It is a temporary (307) redirect while the new domain settles; make it permanent
// (`permanent: true`) once nuevaai.org has served the site reliably for a while.
const config: NextConfig = {
  async redirects() {
    return [{ source: '/:path*', has: [{ type: 'host', value: 'nuev-ai.vercel.app' }], destination: 'https://nuevaai.org/:path*', permanent: false }];
  },
};

export default config;
