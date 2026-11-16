import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import {
  CLOUD_PROVIDERS,
  CloudKeySetup,
  CustomEndpointSetup,
  InferenceRecoveryButton,
  InferenceSourceChooser,
  OllamaConsent,
  cloudProvider,
  inferenceSetupReducer,
  persistAndStartInferenceSource,
  type InferenceSourceSubmission,
} from './InferenceSourceSetup';

describe('first-run inference source navigation', () => {
  it('requires a second, explicit confirmation before local setup', () => {
    expect(inferenceSetupReducer('choose', 'choose_ollama')).toBe('ollama');
    expect(inferenceSetupReducer('ollama', 'back')).toBe('choose');
  });

  it('lets custom endpoint setup cancel safely back to the chooser', () => {
    expect(inferenceSetupReducer('choose', 'choose_custom')).toBe('custom');
    expect(inferenceSetupReducer('custom', 'back')).toBe('choose');
  });

  it('opens the cloud key form and cancels back without saving', () => {
    expect(inferenceSetupReducer('choose', 'choose_cloud')).toBe('cloud');
    expect(inferenceSetupReducer('cloud', 'back')).toBe('choose');
  });

  it('renders every source without claiming any has started', () => {
    const html = renderToStaticMarkup(<InferenceSourceChooser onChoose={vi.fn()} />);

    expect(html).toContain('Cloud AI key (free Gemini)');
    expect(html).toContain('Ollama stays off');
    expect(html).toContain('Local Ollama');
    expect(html).toContain('OpenAI-compatible server');
    expect(html).toContain('Nothing is installed, started, or downloaded until you confirm');
    expect(html).not.toContain('Starting local setup');
  });

  it('discloses the model download and offers a no-op back path', () => {
    const html = renderToStaticMarkup(
      <OllamaConsent onBack={vi.fn()} onConfirm={vi.fn()} />,
    );

    expect(html).toContain('may download one model');
    expect(html).toContain('several gigabytes');
    expect(html).toContain('Going back leaves Ollama stopped');
    expect(html).toContain('Use Ollama and continue');
    expect(html).toContain('Back');
  });

  it('requires endpoint coordinates and keeps an explicit cancel control', () => {
    const html = renderToStaticMarkup(
      <CustomEndpointSetup onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );

    expect(html).toContain('Server URL');
    expect(html).toContain('Model ID');
    expect(html).toMatch(/aria-label="Server URL"[^>]*required/);
    expect(html).toMatch(/aria-label="Model ID"[^>]*required/);
    expect(html).toContain('Ollama will not start or download models');
    expect(html).toContain('Cancel');
  });

  it('asks for a Gemini key by default, with where to get one free', () => {
    const html = renderToStaticMarkup(<CloudKeySetup onCancel={vi.fn()} onConfirm={vi.fn()} />);

    expect(html).toContain('Use a cloud AI key');
    expect(html).toMatch(/<option value="gemini" selected="">Google Gemini \(free\)<\/option>/);
    expect(html).toMatch(/aria-label="API key"[^>]*required/);
    expect(html).toContain('type="password"');
    expect(html).toContain('href="https://aistudio.google.com/api-keys"');
    expect(html).toContain('Ollama will not start or download models');
    expect(html).toContain('Cancel');
    // Nothing to submit until a key is pasted.
    expect(html).toMatch(/<button type="submit" disabled=""/);
  });

  it('offers the cloud brains JARVIS can use, matching the desktop app', () => {
    expect(CLOUD_PROVIDERS.map((provider) => provider.id)).toEqual([
      'gemini',
      'openai',
      'anthropic',
      'openrouter',
    ]);
    expect(cloudProvider('openai').label).toBe('OpenAI');
    expect(cloudProvider(undefined).id).toBe('gemini');
    expect(cloudProvider('unknown').id).toBe('gemini');
  });

  it('renders a source-change recovery action with a guarded busy state', () => {
    const ready = renderToStaticMarkup(
      <InferenceRecoveryButton recovering={false} onChange={vi.fn()} />,
    );
    const stopping = renderToStaticMarkup(
      <InferenceRecoveryButton recovering onChange={vi.fn()} />,
    );

    expect(ready).toContain('Change inference source');
    expect(ready).not.toMatch(/ disabled(?:=""|>)/);
    expect(stopping).toContain('Stopping setup...');
    expect(stopping).toMatch(/ disabled(?:=""|>)/);
  });
});

describe('first-run inference source persistence', () => {
  it('persists the exact choice before requesting backend startup', async () => {
    const calls: string[] = [];
    const persist = vi.fn(async (source: InferenceSourceSubmission) => {
      calls.push(`persist:${source.kind}`);
    });
    const start = vi.fn(async () => {
      calls.push('start');
    });

    await persistAndStartInferenceSource(
      { kind: 'custom', host: 'http://localhost:1234', model: 'local-model', engine: 'lmstudio' },
      persist,
      start,
    );

    expect(calls).toEqual(['persist:custom', 'start']);
    expect(persist).toHaveBeenCalledWith({
      kind: 'custom',
      host: 'http://localhost:1234',
      model: 'local-model',
      engine: 'lmstudio',
    });
  });

  it('stages a cloud key with its provider before starting', async () => {
    const calls: string[] = [];
    const persist = vi.fn(async (source: InferenceSourceSubmission) => {
      calls.push(`persist:${source.kind}:${source.engine}`);
    });
    const start = vi.fn(async () => {
      calls.push('start');
    });

    await persistAndStartInferenceSource(
      { kind: 'cloud', engine: 'gemini', apiKey: 'AIza-test' },
      persist,
      start,
    );

    expect(calls).toEqual(['persist:cloud:gemini', 'start']);
    expect(persist).toHaveBeenCalledWith({ kind: 'cloud', engine: 'gemini', apiKey: 'AIza-test' });
  });

  it('does not start anything when persistence fails', async () => {
    const persist = vi.fn(async () => {
      throw new Error('secure storage unavailable');
    });
    const start = vi.fn(async () => {});

    await expect(
      persistAndStartInferenceSource({ kind: 'ollama' }, persist, start),
    ).rejects.toThrow('secure storage unavailable');
    expect(start).not.toHaveBeenCalled();
  });

  it('rolls a staged choice back when startup rejects', async () => {
    const calls: string[] = [];
    const persist = vi.fn(async () => {
      calls.push('persist');
    });
    const start = vi.fn(async () => {
      calls.push('start');
      throw new Error('endpoint unavailable');
    });
    const rollback = vi.fn(async () => {
      calls.push('rollback');
    });

    await expect(
      persistAndStartInferenceSource(
        { kind: 'custom', host: 'http://localhost:1234', model: 'missing' },
        persist,
        start,
        rollback,
      ),
    ).rejects.toThrow('endpoint unavailable');
    expect(calls).toEqual(['persist', 'start', 'rollback']);
  });
});
