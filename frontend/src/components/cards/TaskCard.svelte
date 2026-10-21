<script lang="ts">
  import Icon from '../Icon.svelte';
  import TaskControls from '../TaskControls.svelte';
  import ArtifactCard from './ArtifactCard.svelte';
  import { duration } from '../../lib/format';
  import { renderMarkdown } from '../../lib/markdown';
  import { isActive, lastLog } from '../../lib/reducer';
  import { app } from '../../lib/store.svelte';
  import type { Task } from '../../lib/types';
  import { stateTone } from '../../lib/ui';

  let { task }: { task: Task } = $props();

  const active = $derived(isActive(task));
  const last = $derived(lastLog(app.data, task.id));
  const artifacts = $derived(task.artifact_ids.map((id) => app.data.artifacts[id]).filter(Boolean));
  let fetched = false;

  $effect(() => {
    // logs aren't in the thread snapshot: fetch them once for a live task
    if (active && !fetched && !app.data.logs[task.id]) {
      fetched = true;
      app.loadTask(task.id).catch(() => {});
    }
  });
</script>

<article class="card task" class:active aria-label="Task: {task.title}">
  <header>
    <span class="icon" class:spin={task.state === 'running'}><Icon name={active ? 'refresh' : task.state === 'done' ? 'check-circle' : 'circle'} size={17} /></span>
    <div class="head">
      <h3>{task.title}</h3>
      <div class="meta">
        <span class="badge {stateTone(task.state)}">{task.state}</span>
        <span class="faint">{task.harness}</span>
        {#if task.steps}<span class="faint">· {task.steps} steps</span>{/if}
        {#if task.started_at}<span class="faint">· {duration(task.started_at, task.finished_at, app.now)}</span>{/if}
      </div>
    </div>
  </header>

  {#if active && last}
    <p class="log" aria-live="polite"><span class="kind">{last.kind}</span> {last.text}</p>
  {/if}
  {#if task.summary && !active}<div class="md summary">{@html renderMarkdown(task.summary)}</div>{/if}
  {#if task.error}<p class="error">{task.error}</p>{/if}
  {#if active}<TaskControls {task} steerable={task.harness !== 'cli'} />{/if}
  {#if artifacts.length}
    <div class="artifacts">
      {#each artifacts as a (a.id)}<ArtifactCard artifact={a} compact />{/each}
    </div>
  {/if}
</article>

<style>
  .task {
    padding: 14px 16px;
  }
  .task.active {
    border-left: 4px solid var(--info);
  }
  header {
    display: flex;
    gap: 10px;
    align-items: flex-start;
  }
  .icon {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: 9px;
    background: var(--info-soft);
    color: var(--info);
    flex: none;
  }
  .spin :global(svg) {
    animation: spin 2.4s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .head {
    flex: 1;
    min-width: 0;
  }
  h3 {
    font-size: 15px;
    font-weight: 650;
  }
  .meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
    font-size: 12.5px;
    margin-top: 2px;
  }
  .log {
    margin: 10px 0 0;
    font-size: 13px;
    color: var(--text-2);
    font-family: var(--mono);
    background: var(--surface-2);
    padding: 6px 10px;
    border-radius: var(--radius-sm);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .kind {
    color: var(--text-3);
    margin-right: 4px;
  }
  .summary {
    margin-top: 10px;
    font-size: 14px;
  }
  .error {
    color: var(--danger);
    font-size: 13.5px;
    margin: 8px 0 0;
  }
  .artifacts {
    display: grid;
    gap: 8px;
    margin-top: 12px;
  }
</style>
