import { firstValue, flatten, mediaUrl, mediaPoster, relativeAge, todayish } from '../shared/html.js';

const asArray = value => Array.isArray(value) ? value : [];
const asObject = value => value && typeof value === 'object' ? value : {};

export function searchResultVm(row) {
  const source = asObject(row);
  const media = asArray(source.media || source.media_items || source.images).map((item,index) => ({...item, position:item.position ?? index}));
  return {
    ...source,
    advertId:String(source.advert_id ?? source.external_listing_id ?? source.id ?? ''),
    personId:Number(source.person_id || 0) || null,
    name:source.name || source.display_name || 'Unbekannt',
    age:source.age || firstValue(source.traits,['Alter','alter']) || '',
    city:source.city || source.location || '',
    thumbnail:source.thumbnail_url || mediaUrl(media[0]) || '',
    media,
    hasVideo:Boolean(source.has_video || media.some(x => String(x.kind).toUpperCase() === 'VIDEO')),
    preference:source.preference || 'NONE',
    favorite:Boolean(source.favorite),
    status:source.current_status || source.status || 'UNKNOWN',
    profileUrl:source.profile_url || source.current_url || ''
  };
}

export function profileBundleVm(bundle, fallback={}) {
  const b = asObject(bundle);
  const identity = asObject(b.identity);
  const listing = asObject(b.listing);
  const profile = asObject(b.profile || b.snapshot || b.latest_snapshot);
  const media = asArray(profile.media || b.media).map((item,index) => ({
    ...item,
    position:item.position ?? index,
    kind:String(item.kind || 'IMAGE').toUpperCase(),
    src:mediaUrl(item),
    poster:mediaPoster(item)
  }));
  return {
    personId:Number(identity.person_id || fallback.personId || fallback.person_id || 0) || null,
    advertId:String(listing.advert_id || fallback.advertId || fallback.advert_id || ''),
    name:profile.name || fallback.name || identity.display_name || 'Unbekannt',
    age:firstValue(profile.traits,['Alter','alter','Age']) || fallback.age || '',
    city:profile.city || fallback.city || '',
    description:profile.description || '',
    traits:asObject(profile.traits),
    services:asObject(profile.services),
    openingTimes:asObject(profile.opening_times),
    media,
    hasVideo:media.some(x => String(x.kind).toUpperCase() === 'VIDEO'),
    identity,
    listing,
    profile,
    preference:identity.preference || fallback.preference || 'NONE',
    favorite:Boolean(identity.favorite ?? fallback.favorite),
    listingCount:Number(identity.known_listing_count || fallback.known_listing_count || 1),
    reappearCount:Number(identity.reappear_count || fallback.reappear_count || 0),
    personFirstSeen:identity.person_first_seen_at || fallback.person_first_seen_at,
    personLastSeen:identity.person_last_seen_at || fallback.person_last_seen_at,
    activeToday:todayish(listing.last_seen_at || fallback.listing_last_seen_at || fallback.person_last_seen_at),
    sourceUrl:profile.source_url || listing.current_url || fallback.profileUrl || ''
  };
}

export function personDetailVm(raw) {
  const d = asObject(raw);
  const person = asObject(d.person);
  const user = asObject(d.user_state);
  const snapshot = asObject(d.latest_snapshot || d.profile || d.snapshot || d.listings?.[0]?.snapshot);
  const media = asArray(snapshot.media || d.media).map((item,index)=>({...item,position:item.position ?? index,kind:String(item.kind || 'IMAGE').toUpperCase(),src:mediaUrl(item),poster:mediaPoster(item)}));
  return {
    raw:d,
    person,
    userState:user,
    personId:Number(person.person_id || 0),
    name:snapshot.name || person.display_name || `Person #${person.person_id || '?'}`,
    age:firstValue(snapshot.traits,['Alter','alter','Age']) || '',
    city:snapshot.city || '',
    description:snapshot.description || '',
    traits:asObject(snapshot.traits),
    services:asObject(snapshot.services),
    openingTimes:asObject(snapshot.opening_times),
    snapshot,
    media,
    listings:asArray(d.listings),
    changes:asArray(d.changes || d.history),
    prices:asArray(d.price_lists || d.prices),
    encounters:asArray(d.encounters),
    analytics:asObject(d.analytics),
    identifiers:asArray(d.identifiers),
    sourceProfiles:asArray(d.source_profiles),
    preference:user.preference || 'NONE',
    favorite:Boolean(user.favorite),
    repeatInterest:user.repeat_interest || 'UNKNOWN',
    privateNote:user.private_note || ''
  };
}

export function listItemVm(item) {
  const x = asObject(item);
  return {
    ...x,
    personId:Number(x.person_id || 0), advertId:String(x.latest_advert_id || x.advert_id || ''),
    name:x.name || x.display_name || `Person #${x.person_id || '?'}`,
    city:x.city || 'Ort unbekannt', thumbnail:x.thumbnail_url || '', status:x.status || 'UNKNOWN',
    favorite:Boolean(x.favorite), preference:x.preference || 'NONE', listingCount:Number(x.listing_count || 1)
  };
}

export function threadVm(thread) {
  const t = asObject(thread);
  return {
    ...t,
    threadId:Number(t.thread_id || 0),
    personId:Number(t.person_id || 0) || null,
    name:t.display_name || t.name || t.contact_name || t.phone || 'WhatsApp',
    avatar:t.avatar_url || t.profile_picture_url || t.thumbnail_url || '',
    preview:t.last_message?.text || t.last_message_text || t.preview || (t.last_message?.message_type ? `[${t.last_message.message_type}]` : ''),
    time:t.last_message_at || t.updated_at || t.last_message?.timestamp || '',
    unread:Number(t.unread_count || t.unread || 0),
    assigned:Boolean(t.person_id),
    preference:t.preference || 'NONE', favorite:Boolean(t.favorite)
  };
}

export function messageVm(message) {
  const m = asObject(message);
  return {
    ...m,
    id:Number(m.message_id || 0),
    direction:String(m.direction || 'IN').toUpperCase(),
    type:String(m.message_type || 'TEXT').toUpperCase(),
    text:m.text || m.caption || '',
    timestamp:m.timestamp || m.created_at || '',
    status:String(m.status || ''),
    replyTo:m.reply_to || m.reply_to_message || null,
    reactions:asArray(m.reactions),
    mediaUrl:m.media_url || (m.message_id ? `/api/messaging/messages/${m.message_id}/media` : ''),
    viewOnce:Boolean(m.view_once),
    deleted:String(m.status || '').toUpperCase() === 'DELETED'
  };
}

export function threadDetailVm(payload) {
  const p = asObject(payload);
  const thread = threadVm(p.thread || p);
  const messages = asArray(p.messages || p.items).map(messageVm);
  return {...p, ...thread, messages, endpoints:asArray(p.endpoints), selectedEndpoint:p.selected_endpoint || p.endpoint || null, hasMore:Boolean(p.has_more), nextBefore:p.next_before || null};
}

export function filterPayload(filters) {
  return {
    city:filters.city || 'Leipzig', radius:Number(filters.radius || 20), category:filters.category || '',
    age_min:Number(filters.ageMin || 18), age_max:Number(filters.ageMax || 60),
    height_min:Number(filters.heightMin || 140), height_max:Number(filters.heightMax || 200),
    selected:Object.fromEntries(Object.entries(filters.selected || {}).filter(([,values]) => Array.isArray(values) && values.length).map(([key,values]) => [key,[...values].sort()]))
  };
}

export function profileFilterEvidence(detail, filters) {
  const selected = new Set(Object.values(filters.selected || {}).flat().map(value => String(value).toLocaleLowerCase('de-DE')));
  const all = [...flatten(detail.services), ...flatten(detail.traits), ...flatten(detail.openingTimes)].map(value => String(value));
  return all.map(label => ({label,matchesFilter:selected.has(label.toLocaleLowerCase('de-DE'))}));
}

export function statusLabel(status) {
  const s = String(status || '').toUpperCase();
  if (s === 'CONNECTED') return 'Verbunden';
  if (s === 'NOT_CONFIGURED' || s === 'LOGGED_OUT') return 'Nicht verbunden';
  if (s === 'RECONNECTING') return 'Verbindung wird wiederhergestellt';
  if (s === 'DEPENDENCY_MISSING') return 'WhatsApp-Komponente fehlt';
  return s ? s.replaceAll('_',' ') : 'Unbekannt';
}

export function listingAge(item) {
  const value = item?.published_at || item?.first_seen_at || item?.listing_first_seen_at;
  return value ? relativeAge(value) : '';
}
