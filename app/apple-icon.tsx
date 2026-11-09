import { ImageResponse } from 'next/og';
import { brandIcon } from '@/lib/brand-icon';

export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

// iOS rounds the corners itself, so the tile is full-bleed
export default function AppleIcon() {
  return new ImageResponse(brandIcon(180, { glyph: 0.56 }), { ...size });
}
