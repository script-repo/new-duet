<script lang="ts">
  import Icon from './Icon.svelte';
  import { app } from '../lib/store.svelte';
  import { startVoice, voiceMode, type VoiceSession } from '../lib/voice';

  let text = $state('');
  let sending = $state(false);
  let harness = $state('');
  let listening = $state<'listening' | 'transcribing' | 'idle'>('idle');
  let session: VoiceSession | null = null;
  let base = '';
  let el: HTMLTextAreaElement | undefined = $state();

  const mode = voiceMode();
  const harnesses = $derived((app.config?.harnesses ?? []).filter((h) => h.available));

  function grow() {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }

  $effect(() => {
    void text;
    grow();
  });

  async function send(e?: Event) {
    e?.preventDefault();
    const t = text.trim();
    if (!t || sending) return;
    session?.stop();
    sending = true;
    if (await app.send(t, harness || undefined)) text = '';
    sending = false;
    el?.focus();
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) send(e);
  }

  async function toggleMic() {
    if (listening !== 'idle') {
      session?.stop();
      return;
    }
    base = text && !text.endsWith(' ') ? text + ' ' : text;
    session = await startVoice({
      onText: (final, interim) => (text = base + final + interim),
      onState: (s) => {
        listening = s;
        if (s === 'idle') session = null;
      },
      onError: (m) => app.toast({ title: 'Voice input', body: m, urgency: 'normal', kind: 'error' }),
    });
  }
</script>

<form class="composer" onsubmit={send}>
  <label for="composer-input" class="sr-only">Message Tandem</label>
  <textarea
    id="composer-input"
    bind:this={el}
    bind:value={text}
    onkeydown={onKey}
    rows="1"
    placeholder={listening === 'listening' ? 'Listening…' : listening === 'transcribing' ? 'Transcribing…' : 'Ask Tandem anything…'}
    disabled={sending}
  ></textarea>
  <div class="tools">
    {#if harnesses.length > 1}
      <label class="sr-only" for="composer-harness">Harness for new tasks</label>
      <select id="composer-harness" class="harness" bind:value={harness} title="Harness for tasks this message starts">
        <option value="">Default harness</option>
        {#each harnesses as h (h.name)}<option value={h.name}>{h.display_name}</option>{/each}
      </select>
    {/if}
    <span class="spacer"></span>
    {#if mode !== 'none'}
      <button
        type="button"
        class="icon-btn mic"
        class:on={listening !== 'idle'}
        onclick={toggleMic}
        disabled={listening === 'transcribing'}
        aria-pressed={listening === 'listening'}
        aria-label={listening === 'listening' ? 'Stop voice input' : 'Voice input'}
        title={mode === 'speech' ? 'Dictate' : 'Record and transcribe'}
      >
        <Icon name={listening === 'listening' ? 'stop' : 'mic'} />
      </button>
    {/if}
    <button type="submit" class="send" disabled={sending || !text.trim()} aria-label="Send">
      <Icon name="send" size={17} />
    </button>
  </div>
</form>

<style>
  .composer {
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 22px;
    box-shadow: var(--shadow);
    padding: 8px 8px 8px 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .composer:focus-within {
    border-color: var(--accent);
    box-shadow: var(--shadow), var(--focus);
  }
  textarea {
    border: none;
    background: transparent;
    resize: none;
    outline: none;
    padding: 6px 0 2px;
    line-height: 1.45;
    max-height: 220px;
    font-size: 15.5px;
  }
  textarea:focus-visible {
    box-shadow: none;
  }
  .tools {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .spacer {
    flex: 1;
  }
  .harness {
    border: none;
    background: var(--surface-2);
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 12.5px;
    color: var(--text-2);
  }
  .mic.on {
    background: var(--danger-soft);
    color: var(--danger);
    animation: pulse 1.4s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      box-shadow: 0 0 0 6px color-mix(in srgb, var(--danger) 18%, transparent);
    }
  }
  .send {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 999px;
    border: none;
    background: var(--accent);
    color: var(--accent-text);
  }
  .send:hover:not(:disabled) {
    background: var(--accent-hover);
  }
  .send:disabled {
    background: var(--surface-3);
    color: var(--text-3);
    opacity: 1;
  }
</style>
