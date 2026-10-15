import { escapeAttr } from './html.js';
const MAP = Object.freeze({
  search:'search', filter:'filter', heart:'heart', 'heart-fill':'heart-fill', chat:'message', message:'message', settings:'settings', cards:'compass', list:'list', pin:'map-pin', location:'map-pin', home:'home', eye:'eye', back:'chevron-left', 'chevron-left':'chevron-left', 'chevron-right':'chevron-right', more:'more', phone:'phone', send:'send', clip:'plus', tag:'tag', star:'star', 'star-fill':'star-fill', user:'user', users:'users', info:'info', 'thumb-up':'heart-fill', 'thumb-down':'x', sliders:'sliders', clock:'clock', image:'image', video:'video', database:'database', trash:'trash', refresh:'refresh', check:'check', calendar:'calendar', sparkles:'sparkles', archive:'archive', note:'note', plus:'plus', x:'x', undo:'undo', play:'play', external:'external', shield:'shield', edit:'edit', mic:'message', chart:'chart'
});
const INLINE = Object.freeze({
  grid: cls => `<svg${cls ? ` class="${escapeAttr(cls)}"` : ''} aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="14" y="3" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="14" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="14" y="14" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/></svg>`
});
export function icon(name, cls='') {
  if (INLINE[name]) return INLINE[name](cls);
  const id = MAP[name] || name;
  return `<svg${cls ? ` class="${escapeAttr(cls)}"` : ''} aria-hidden="true"><use href="/static/icons.svg#${escapeAttr(id)}"></use></svg>`;
}
