import type { MetadataRoute } from 'next';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/', disallow: ['/insights', '/api'] },
    sitemap: 'https://nuev-ai.vercel.app/sitemap.xml',
  };
}
