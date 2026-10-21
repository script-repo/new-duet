<script lang="ts">
  import Logo from './Logo.svelte';
  import { app } from '../lib/store.svelte';

  let token = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    busy = true;
    error = await app.signIn(token);
    busy = false;
  }
</script>

<main class="signin">
  <form class="card" onsubmit={submit}>
    <Logo size={48} />
    <h1>Sign in to Tandem</h1>
    <p class="muted">This server asks for its access token (<span class="mono">TANDEM_AUTH_TOKEN</span>).</p>
    <label for="token">Access token</label>
    <!-- svelte-ignore a11y_autofocus -->
    <input id="token" class="input" type="password" autocomplete="current-password" bind:value={token} disabled={busy} autofocus
      aria-invalid={error ? 'true' : undefined} aria-describedby={error ? 'token-error' : undefined} />
    {#if error}<p id="token-error" class="error" role="alert">{error}</p>{/if}
    <button class="btn primary" type="submit" disabled={busy || !token.trim()}>{busy ? 'Signing in…' : 'Sign in'}</button>
  </form>
</main>

<style>
  .signin {
    min-height: 100%;
    display: grid;
    place-items: center;
    padding: 24px;
  }
  form {
    width: min(400px, 100%);
    padding: 30px 28px;
    display: grid;
    gap: 10px;
    justify-items: start;
  }
  h1 {
    font-family: var(--serif);
    font-size: 24px;
    font-weight: 600;
    margin-top: 6px;
  }
  p {
    margin: 0 0 6px;
    font-size: 14px;
  }
  label {
    font-size: 13px;
    font-weight: 600;
  }
  .btn {
    width: 100%;
    margin-top: 6px;
    min-height: 42px;
  }
  .error {
    color: var(--danger);
  }
</style>
