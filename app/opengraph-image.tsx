import { ImageResponse } from 'next/og';

export const alt = 'Nueva AI: ask anything about Nueva.';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

const NAVY = '#0E2145';
const BLUE = '#1B7CFF';
const FONT = 'system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', background: '#F4F1EB', padding: '64px 80px', fontFamily: FONT }}>
        <div style={{ display: 'flex', fontSize: 52, fontWeight: 800, letterSpacing: -1, color: NAVY }}>
          <span>Nueva</span>
          <span style={{ color: BLUE }}>AI</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', fontSize: 92, fontWeight: 800, lineHeight: 1.02, letterSpacing: -3, color: NAVY, width: 600 }}>Ask anything about Nueva.</div>
          <div style={{ display: 'flex', flexDirection: 'column', width: 430 }}>
            <div style={{ display: 'flex', alignSelf: 'flex-end', background: BLUE, color: '#fff', fontSize: 38, fontWeight: 600, padding: '20px 32px', borderRadius: 40, borderBottomRightRadius: 10 }}>what&apos;s for lunch</div>
            <div style={{ display: 'flex', alignSelf: 'flex-start', marginTop: 20, background: '#E6E6EB', color: '#111', fontSize: 34, fontWeight: 500, lineHeight: 1.25, padding: '22px 32px', borderRadius: 40, borderBottomLeftRadius: 10 }}>Here&apos;s today&apos;s menu for your campus.</div>
          </div>
        </div>
        <div style={{ display: 'flex', fontSize: 30, fontWeight: 600, color: NAVY, opacity: 0.7 }}>nuevaai.org</div>
      </div>
    ),
    { ...size },
  );
}
