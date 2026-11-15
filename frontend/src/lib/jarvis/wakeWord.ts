/**
 * Wake-word spotting on speech-to-text transcripts.
 *
 * TypeScript port of `openjarvis/speech/wake_word.py` so the HUD and
 * `jarvis listen` react to exactly the same phrases. JARVIS only acts when
 * addressed by name ("Jarvis, open Spotify" or "What's the time, Jarvis?"),
 * and accepts common speech-recogniser misspellings of the name.
 */

const KNOWN_VARIANTS: Record<string, string[]> = {
  jarvis: [
    'jarvis',
    'jarviss',
    'jervis',
    'jarves',
    'jarvus',
    'javis',
    'jarbis',
    'garvis',
    'charvis',
    'service', // "hey jarvis" -> "hey service" with small Whisper models
  ],
};
// Ordinary words that only count after a greeting ("hey service").
const GREETING_ONLY = new Set(['service']);
const GREETINGS = ['hey', 'hi', 'hello', 'ok', 'okay', 'yo', 'oi', 'so'];
const FILLERS = new Set(['um', 'uh', 'er', 'ah', 'oh', 'hmm', 'please', 'and', 'now']);
const SKIPPABLE = new Set([...FILLERS, ...GREETINGS]);
const EDGE = /^[\s,.!?;:\-\u2013\u2014]+|[\s,.!?;:\-\u2013\u2014]+$/g;

const STOP_PHRASES = new Set([
  'goodbye',
  'good bye',
  'bye bye',
  'stop listening',
  'go to sleep',
  'shut down',
  'exit',
  'quit',
]);
const CANCEL_PHRASES = new Set([
  'never mind',
  'nevermind',
  'cancel',
  'forget it',
  'nothing',
  'thats all',
]);
// Whisper's favourite hallucinations on silence or background noise.
const NOISE_TRANSCRIPTS = new Set([
  'you',
  'thank you',
  'thanks for watching',
  'thank you for watching',
  'please subscribe',
  'bye',
  'music',
  'applause',
  'silence',
  'laughter',
]);

/** Lower-case, strip accents and punctuation, collapse whitespace. */
export function normalise(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[\u2019']/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function words(text: string): string[] {
  return normalise(text).split(' ').filter(Boolean);
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Trim punctuation plus leading greetings/fillers, keeping the wording. */
function stripCommand(raw: string): string {
  let text = raw.replace(EDGE, '');
  for (;;) {
    const lead = /^(\w+)\b[\s,.!?;:]*/.exec(text);
    if (!lead || !SKIPPABLE.has(lead[1].toLowerCase())) break;
    text = text.slice(lead[0].length);
  }
  for (;;) {
    const tail = /[\s,.!?;:]*\b(\w+)$/.exec(text);
    if (!tail || !FILLERS.has(tail[1].toLowerCase())) break;
    text = text.slice(0, tail.index);
  }
  return text.replace(EDGE, '');
}

export interface WakeMatch {
  /** The request without the wake word ("" = just the name). */
  command: string;
  /** The full transcript. */
  heard: string;
}

export class WakeWordDetector {
  readonly wakeWord: string;
  readonly variants: ReadonlySet<string>;
  private readonly pattern: RegExp;

  constructor(wakeWord = 'jarvis', extraVariants: string[] = []) {
    const word = normalise(wakeWord) || 'jarvis';
    const variants = new Set(KNOWN_VARIANTS[word] ?? [word]);
    variants.add(word);
    for (const extra of extraVariants) {
      const clean = normalise(extra);
      if (clean) variants.add(clean);
    }
    this.wakeWord = word;
    this.variants = variants;
    const names = [...variants]
      .sort((a, b) => b.length - a.length)
      .map((v) => v.split(' ').map(escapeRegExp).join('\\s*'))
      .join('|');
    this.pattern = new RegExp(
      `(?:\\b(?<greeting>${GREETINGS.join('|')})\\b[\\s,.!]*)?\\b(?<name>${names})\\b`,
      'gi',
    );
  }

  /**
   * Return the request if `transcript` addresses JARVIS, else `null`.
   *
   * The name must open or close the sentence (after at most one lead-in
   * word, as in "Okay so Jarvis ..."), so merely mentioning Jarvis
   * mid-sentence does not wake it.
   */
  find(transcript: string): WakeMatch | null {
    const original = (transcript ?? '').trim();
    if (!original) return null;
    for (const match of original.matchAll(this.pattern)) {
      const name = normalise(match.groups?.name ?? '');
      if (GREETING_ONLY.has(name) && !match.groups?.greeting) continue;
      const start = match.index ?? 0;
      const beforeRaw = original.slice(0, start);
      const afterRaw = original.slice(start + match[0].length);
      const leading = words(beforeRaw).filter((w) => !SKIPPABLE.has(w));
      const after = words(afterRaw);
      if (leading.length === 0) return { command: stripCommand(afterRaw), heard: original };
      if (after.every((w) => FILLERS.has(w))) {
        return { command: stripCommand(beforeRaw), heard: original };
      }
      if (leading.length === 1 && after.length >= 2) {
        return { command: stripCommand(afterRaw), heard: original };
      }
    }
    return null;
  }
}

/** True for "goodbye", "stop listening" and similar. */
export function isStopCommand(text: string): boolean {
  let clean = normalise(text);
  for (const prefix of ['jarvis ', 'ok ', 'okay ']) {
    if (clean.startsWith(prefix)) clean = clean.slice(prefix.length);
  }
  if (clean.endsWith(' jarvis')) clean = clean.slice(0, -' jarvis'.length);
  return STOP_PHRASES.has(clean);
}

/** True for "never mind", "cancel" and similar. */
export function isCancelCommand(text: string): boolean {
  return CANCEL_PHRASES.has(normalise(text));
}

/** True for empty or junk transcripts produced from background noise. */
export function isNoiseTranscript(text: string): boolean {
  const clean = normalise(text);
  return clean.length < 2 || NOISE_TRANSCRIPTS.has(clean);
}
