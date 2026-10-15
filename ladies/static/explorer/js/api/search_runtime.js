import { request } from './client.js';

export function searchTransient(payload,{signal}={}) {
  return request('/api/search-transient',{method:'POST',body:payload,signal});
}
