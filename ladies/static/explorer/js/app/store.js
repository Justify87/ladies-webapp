import { reducer } from './reducer.js';
import { initialState } from './state.js';

export function createStore() {
  let state = structuredClone(initialState);
  const listeners = new Set();
  return Object.freeze({
    getState:() => state,
    dispatch(action) { const next = reducer(state, action); if (next !== state) { state = next; listeners.forEach(fn => fn(state,action)); } return action; },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }
  });
}
