import { describe, expect, it } from 'vitest';
import { friendlyError, isDesktop } from './api';

describe('JARVIS desktop API helpers', () => {
  it('formats native and unknown errors without losing a useful message', () => {
    expect(friendlyError(new Error('Gemini key rejected'))).toBe('Gemini key rejected');
    expect(friendlyError('Microphone denied')).toBe('Microphone denied');
    expect(friendlyError({})).toBe('Something went wrong. Please try again.');
  });

  it('detects a Tauri desktop runtime safely', () => {
    expect(isDesktop()).toBe(false);
  });
});
