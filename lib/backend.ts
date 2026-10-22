/** Server-side only. The Python backend base URL (e.g. https://nuevai-backend.onrender.com). */
export const BACKEND = (
  process.env.BACKEND_URL ??
  (process.env.CHAT_API_URL ?? 'https://nuevai-backend.onrender.com/api/chat').replace(/\/api\/chat\/?$/, '')
).replace(/\/$/, '');
