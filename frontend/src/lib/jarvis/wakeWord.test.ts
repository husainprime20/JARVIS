import { describe, expect, it } from 'vitest';
import {
  WakeWordDetector,
  isCancelCommand,
  isNoiseTranscript,
  isStopCommand,
  normalise,
} from './wakeWord';

// Same cases as tests/speech/test_wake_word.py: the HUD and `jarvis listen`
// must react to exactly the same phrases.
const detector = new WakeWordDetector('jarvis');

describe('WakeWordDetector', () => {
  it.each([
    ['Jarvis, open Spotify.', 'open Spotify'],
    ['jarvis turn the volume up', 'turn the volume up'],
    ['Hey Jarvis! Open github.com please.', 'Open github.com'],
    ["What's the time, Jarvis?", "What's the time"],
    ["um, jarvis, uh, what's 25 times 4?", "what's 25 times 4"],
    ['Please Jarvis, lock the screen', 'lock the screen'],
    ["Okay so Jarvis what's the weather in Rawalpindi", "what's the weather in Rawalpindi"],
    ['Jervis, play Despacito on YouTube', 'play Despacito on YouTube'],
    ['Hey service, play some music', 'play some music'],
    ['JARVIS: remind me who I am', 'remind me who I am'],
  ])('extracts the request from %j', (heard, command) => {
    expect(detector.find(heard)).toEqual({ command, heard });
  });

  it.each(['Jarvis?', 'Jarvis.', 'Hey, jarvis', 'Hey Jarvis!'])('wakes on the name alone: %j', (heard) => {
    expect(detector.find(heard)?.command).toBe('');
  });

  it.each([
    '',
    '   ',
    'Open Spotify',
    'I was telling my friend about Jarvis yesterday and he laughed',
    'The customer service was terrible',
    'jarvisville is a town',
    'Thank you.',
  ])('ignores speech that does not address JARVIS: %j', (heard) => {
    expect(detector.find(heard)).toBeNull();
  });

  it('supports a custom wake word with extra spellings', () => {
    const friday = new WakeWordDetector('Friday', ['fry day']);
    expect(friday.find('Friday, play music')?.command).toBe('play music');
    expect(friday.find('fry day, what time is it')?.command).toBe('what time is it');
    expect(friday.find('Jarvis, play music')).toBeNull();
    expect(friday.wakeWord).toBe('friday');
  });

  it('uses the first address and keeps later mentions in the request', () => {
    expect(detector.find("Jarvis, what's my name? Jarvis is cool.")?.command).toBe(
      "what's my name? Jarvis is cool",
    );
  });
});

describe('command phrases', () => {
  it.each([
    ['Goodbye, Jarvis.', true],
    ['Jarvis, stop listening', true],
    ['go to sleep', true],
    ['goodbye my friend', false],
    ['open Spotify', false],
  ])('isStopCommand(%j) is %s', (text, expected) => {
    expect(isStopCommand(text)).toBe(expected);
  });

  it.each([
    ['Never mind.', true],
    ['cancel', true],
    ['cancel my 3pm meeting', false],
  ])('isCancelCommand(%j) is %s', (text, expected) => {
    expect(isCancelCommand(text)).toBe(expected);
  });

  it.each([
    ['', true],
    ['.', true],
    ['Thank you.', true],
    ['Thanks for watching!', true],
    ['open Spotify', false],
    ['thank you jarvis', false],
  ])('isNoiseTranscript(%j) is %s', (text, expected) => {
    expect(isNoiseTranscript(text)).toBe(expected);
  });

  it('normalises accents, apostrophes and punctuation', () => {
    expect(normalise("  Café — what’s  UP?! ")).toBe('cafe whats up');
  });
});
