<script lang="ts">
  import Icon from '../Icon.svelte';
  import { artifactUrls } from '../../lib/api';
  import { formatBytes, relativeTime } from '../../lib/format';
  import { app } from '../../lib/store.svelte';
  import type { Artifact } from '../../lib/types';
  import { artifactIcon } from '../../lib/ui';

  let { artifact, compact = false }: { artifact: Artifact; compact?: boolean } = $props();
  const urls = $derived(artifactUrls(artifact.id));
</script>

<article class="artifact" class:card={!compact} class:compact aria-label="{artifact.kind} file: {artifact.title}">
  <span class="kind kind-{artifact.kind}"><Icon name={artifactIcon(artifact.kind)} size={18} /><span class="ext">{artifact.kind}</span></span>
  <div class="body">
    <h3>{artifact.title}</h3>
    <p class="faint">{artifact.filename} · {formatBytes(artifact.size)} · {relativeTime(artifact.created_at, app.now)}</p>
  </div>
  <div class="actions">
    <button class="btn sm" aria-label="Preview {artifact.title}" onclick={() => (app.preview = artifact)}><Icon name="eye" size={14} /><span class="label">Preview</span></button>
    <a class="btn sm ghost" href={urls.download} download={artifact.filename} aria-label="Download {artifact.filename}">
      <Icon name="download" size={14} />
    </a>
  </div>
</article>

<style>
  .artifact {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
  }
  .compact {
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
  }
  .kind {
    position: relative;
    display: grid;
    place-items: center;
    width: 40px;
    height: 44px;
    border-radius: 8px;
    background: var(--surface-2);
    color: var(--text-2);
    flex: none;
  }
  .ext {
    position: absolute;
    bottom: -5px;
    font-size: 9.5px;
    font-weight: 800;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 0 4px;
    border-radius: 4px;
    background: var(--text-2);
    color: var(--surface);
  }
  .kind-xlsx {
    color: var(--ok);
    background: var(--ok-soft);
  }
  .kind-xlsx .ext {
    background: var(--ok);
  }
  .kind-pptx {
    color: var(--accent);
    background: var(--accent-soft);
  }
  .kind-pptx .ext {
    background: var(--accent);
  }
  .kind-docx {
    color: var(--info);
    background: var(--info-soft);
  }
  .kind-docx .ext {
    background: var(--info);
  }
  .body {
    flex: 1;
    min-width: 0;
  }
  h3 {
    font-size: 14.5px;
    font-weight: 650;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  p {
    margin: 1px 0 0;
    font-size: 12.5px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .actions {
    display: flex;
    gap: 4px;
    flex: none;
  }
  a.btn {
    text-decoration: none;
  }
  @media (max-width: 520px) {
    .label {
      display: none;
    }
    .actions .btn {
      padding: 0 9px;
    }
  }
</style>
