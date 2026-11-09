import { ImageResponse } from 'next/og';
import { brandIcon } from '@/lib/brand-icon';

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default function Icon() {
  return new ImageResponse(brandIcon(32, { radius: 0.5, glyph: 0.62 }), { ...size });
}
