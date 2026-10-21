<script lang="ts">
  import { tick } from 'svelte';
  import Composer from './Composer.svelte';
  import Logo from './Logo.svelte';
  import MessageItem from './MessageItem.svelte';
  import { parseTs } from '../lib/format';
  import { app } from '../lib/store.svelte';
  import type { Message } from '../lib/types';

  let scroller: HTMLDivElement | undefined = $state();
  let stick = true;

  const messages = $derived(app.data.messages);
  const lastId = $derived(messages.length ? messages[messages.length - 1].id : 0);

  function dayKey(m: Message): string {
    const t = parseTs(m.created_at);
    return Number.isNaN(t) ? '' : new Date(t).toDateString();
  }

  function dayLabel(m: Message): string {
    const t = parseTs(m.created_at);
    if (Number.isNaN(t)) return '';
    const d = new Date(t);
    const today = new Date();
    const yest = new Date(Date.now() - 86400000);
    if (d.toDateString() === today.toDateString()) return 'Today';
    if (d.toDateString() === yest.toDateString()) return 'Yesterday';
    return d.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
  }

  function onScroll() {
    if (!scroller) return;
    stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  }

  $effect(() => {
    void lastId;
    void app.data.typing;
    if (!stick || !scroller) return;
    void tick().then(() => scroller && (scroller.scrollTop = scroller.scrollHeight));
  });

  async function older() {
    if (!scroller) return;
    const before = scroller.scrollHeight;
    await app.loadOlder();
    await tick();
    scroller.scrollTop += scroller.scrollHeight - before;
  }
</script>

<section class="thread" aria-label="Conversation">
  <div class="scroll" bind:this={scroller} onscroll={onScroll}>
    <div class="inner">
      {#if app.data.hasMore}
        <div class="older">
          <button class="btn sm" onclick={older} disabled={app.loadingOlder}>{app.loadingOlder ? 'Loading…' : 'Earlier messages'}</button>
        </div>
      {/if}
      {#if !messages.length}
        <div class="welcome">
          <Logo size={52} />
          <h2>Quiet until needed.</h2>
          <p class="muted">I watch your inbox and calendar, brief you each morning, and only interrupt when something matters. Ask me to build, draft or chase anything.</p>
        </div>
      {/if}
      <div role="log" aria-live="polite" aria-relevant="additions" aria-label="Messages">
        {#each messages as m, i (m.id)}
          {#if i === 0 || dayKey(m) !== dayKey(messages[i - 1])}
            <div class="day"><span>{dayLabel(m)}</span></div>
          {/if}
          <MessageItem message={m} />
        {/each}
      </div>
      {#if app.data.typing}
        <div class="typing" role="status">
          <Logo size={26} />
          <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
          <span class="sr-only">Tandem is replying</span>
        </div>
      {/if}
    </div>
  </div>
  <div class="composer-wrap">
    <Composer />
  </div>
</section>

<style>
  .thread {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  .scroll {
    flex: 1;
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .inner {
    max-width: 760px;
    margin: 0 auto;
    padding: 18px 18px 8px;
  }
  .older {
    display: flex;
    justify-content: center;
    margin: 4px 0 10px;
  }
  .welcome {
    text-align: center;
    padding: 12vh 12px 20px;
    display: grid;
    justify-items: center;
    gap: 10px;
  }
  .welcome h2 {
    font-family: var(--serif);
    font-size: 26px;
    font-weight: 600;
  }
  .welcome p {
    max-width: 440px;
    margin: 0;
  }
  .day {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 22px 0 6px;
    font-size: 12px;
    font-weight: 650;
    color: var(--text-3);
  }
  .day::before,
  .day::after {
    content: '';
    flex: 1;
    border-top: 1px solid var(--border);
  }
  .typing {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 12px 0;
  }
  .dots {
    display: inline-flex;
    gap: 4px;
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 18px;
    border-top-left-radius: 6px;
  }
  .dots i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-3);
    animation: bounce 1.2s infinite ease-in-out;
  }
  .dots i:nth-child(2) {
    animation-delay: 0.15s;
  }
  .dots i:nth-child(3) {
    animation-delay: 0.3s;
  }
  @keyframes bounce {
    0%,
    80%,
    100% {
      transform: translateY(0);
      opacity: 0.5;
    }
    40% {
      transform: translateY(-4px);
      opacity: 1;
    }
  }
  .composer-wrap {
    max-width: 760px;
    width: 100%;
    margin: 0 auto;
    padding: 8px 16px 16px;
  }
  @media (max-width: 899px) {
    .composer-wrap {
      padding: 6px 10px 10px;
    }
    .inner {
      padding: 12px 12px 6px;
    }
  }
</style>
