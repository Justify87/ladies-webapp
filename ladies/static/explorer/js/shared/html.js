export const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
export const escapeAttr = escapeHtml;
export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export const flatten = value => Object.values(value || {}).flatMap(item => Array.isArray(item) ? item : [item]).filter(Boolean);
export const firstValue = (obj, keys) => {
  for (const key of keys) {
    const value = obj?.[key];
    if (Array.isArray(value) && value.length) return value[0];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return null;
};
export const fmtDate = (value, withTime = true) => {
  if (!value) return '—';
  try { return new Intl.DateTimeFormat('de-DE', withTime ? {dateStyle:'short', timeStyle:'short'} : {dateStyle:'medium'}).format(new Date(value)); }
  catch { return String(value); }
};
export const fmtTime = value => {
  if (!value) return '';
  try { return new Intl.DateTimeFormat('de-DE', {hour:'2-digit', minute:'2-digit'}).format(new Date(value)); }
  catch { return ''; }
};
export const fmtBytes = value => {
  let n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B','KB','MB','GB','TB']; let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${new Intl.NumberFormat('de-DE',{maximumFractionDigits:i > 1 ? 1 : 0}).format(n)} ${units[i]}`;
};
export const euro = value => value === null || value === undefined || value === '' ? '—' : new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(Number(value));
export const relativeAge = value => {
  if (!value) return '';
  const t = new Date(value).getTime(); if (!Number.isFinite(t)) return '';
  const mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
  if (mins < 2) return 'gerade eben';
  if (mins < 60) return `vor ${mins} Min.`;
  const hours = Math.floor(mins / 60); if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.max(1, Math.floor(hours / 24)); if (days < 31) return `vor ${days} ${days === 1 ? 'Tag' : 'Tagen'}`;
  const months = Math.max(1, Math.floor(days / 30)); if (months < 12) return `vor ${months} ${months === 1 ? 'Monat' : 'Monaten'}`;
  const years = Math.max(1, Math.floor(days / 365)); return `vor ${years} ${years === 1 ? 'Jahr' : 'Jahren'}`;
};
export const todayish = value => !!value && (Date.now() - new Date(value).getTime()) < 36 * 60 * 60 * 1000;
export const mediaUrl = media => media?.local_url || media?.url || media?.thumbnail_url || '';
export const mediaPoster = media => media?.poster_local_url || media?.poster_url || media?.thumbnail_url || '';
export const downloadName = (value, fallback='datei') => String(value || fallback).replace(/[^a-z0-9_. -]/gi, '_').slice(0, 120);
