<script lang="ts" module>
  // Simple marks in brand-ish colours: a glyph on a tinted tile, not the vendors' logos.
  const MARKS: Record<string, { bg: string; glyph: string }> = {
    gmail: {
      bg: '#d93f33',
      glyph: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 6.5 8.5-6.5"/>',
    },
    google_calendar: {
      bg: '#1a73e8',
      glyph: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/><path d="M8.5 13h2v5M13.5 13h2.5l-1.5 5"/>',
    },
    slack: {
      bg: '#4a154b',
      glyph: '<path d="M9 3 7 21M17 3l-2 18M3.5 8.5h17M3 15.5h17"/>',
    },
    m365: {
      bg: '#0f6cbd',
      glyph: '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    },
  };
  const BY_PROVIDER: Record<string, string> = { google: 'gmail', slack: 'slack', microsoft: 'm365' };
</script>

<script lang="ts">
  let { type, provider = '', name = '', size = 40 }: { type: string; provider?: string; name?: string; size?: number } = $props();
  const mark = $derived(MARKS[type] ?? MARKS[BY_PROVIDER[provider] ?? '']);
  const initial = $derived((name || type || '?').trim().charAt(0).toUpperCase());
</script>

<span class="mark" class:plain={!mark} style:--size="{size}px" style:--bg={mark?.bg} aria-hidden="true">
  {#if mark}
    <svg viewBox="0 0 24 24" width={size * 0.55} height={size * 0.55} fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">
      {@html mark.glyph}
    </svg>
  {:else}
    {initial}
  {/if}
</span>

<style>
  .mark {
    flex: none;
    width: var(--size);
    height: var(--size);
    border-radius: calc(var(--size) * 0.28);
    display: grid;
    place-items: center;
    background: var(--bg);
    color: #fff;
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.12);
  }
  .plain {
    background: var(--surface-2);
    color: var(--text-2);
    font-weight: 700;
    font-size: calc(var(--size) * 0.42);
    box-shadow: inset 0 0 0 1px var(--border);
  }
</style>
