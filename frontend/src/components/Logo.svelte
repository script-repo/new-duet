<script lang="ts">
  // Two linked rings: the agent and you, riding together.
  let { size = 28 }: { size?: number } = $props();
</script>

<svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
  <rect width="64" height="64" rx="16" fill="var(--accent)" />
  <circle cx="25" cy="32" r="11" fill="none" stroke="var(--accent-text)" stroke-width="5" />
  <circle cx="39" cy="32" r="11" fill="none" stroke="var(--accent-text)" stroke-width="5" opacity="0.8" />
</svg>
