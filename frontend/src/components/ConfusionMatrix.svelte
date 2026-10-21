<script lang="ts">
  import { heatStep, pct, type Matrix, type MatrixCell } from '../lib/evals';

  let { matrix }: { matrix: Matrix } = $props();
  let hover = $state<MatrixCell | null>(null);

  function describe(c: MatrixCell): string {
    const kind = c.correct ? 'correct' : c.missedUrgent ? 'missed urgent' : c.falseAlarm ? 'false alarm' : 'mislabelled';
    return `Gold ${c.gold}, predicted ${c.pred}: ${c.count} (${pct(c.share)} of ${c.gold}), ${kind}`;
  }
</script>

<div class="matrix-wrap">
  <table class="matrix">
    <caption class="sr-only">Confusion matrix: rows are the gold label, columns the predicted label. Diagonal cells are correct.</caption>
    <thead>
      <tr>
        <td class="corner"><span class="axis-y">gold ↓</span><span class="axis-x">predicted →</span></td>
        {#each matrix.labels as l (l)}<th scope="col">{l}</th>{/each}
        <th scope="col" class="total">total</th>
      </tr>
    </thead>
    <tbody>
      {#each matrix.rows as r (r.gold)}
        <tr>
          <th scope="row">{r.gold}</th>
          {#each r.cells as c (c.pred)}
            {@const step = heatStep(c.share, c.count)}
            <td
              class="cell s{step}"
              class:diag={c.correct}
              class:miss={c.missedUrgent && c.count > 0}
              title={describe(c)}
              onpointerenter={() => (hover = c)}
              onpointerleave={() => (hover = null)}
            >
              <span class="n">{c.count}</span>
              {#if c.count}<span class="share">{pct(c.share)}</span>{/if}
              {#if c.correct}<span class="sr-only">(correct)</span>{:else if c.missedUrgent && c.count}<span class="sr-only">(missed urgent)</span>{/if}
            </td>
          {/each}
          <td class="total">{r.total}</td>
        </tr>
      {/each}
    </tbody>
    <tfoot>
      <tr>
        <th scope="row" class="total">total</th>
        {#each matrix.colTotals as t, i (i)}<td class="total">{t}</td>{/each}
        <td class="total"><strong>{matrix.n}</strong></td>
      </tr>
    </tfoot>
  </table>
  <p class="readout" aria-hidden="true">
    {#if hover}{describe(hover)}{:else}Shares are of each gold row. <span class="key diag-key"></span> correct <span class="key miss-key"></span> missed urgent{/if}
  </p>
</div>

<style>
  .matrix-wrap {
    overflow-x: auto;
    --heat: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    .matrix-wrap {
      --heat: #3987e5;
    }
  }
  .matrix {
    border-collapse: separate;
    border-spacing: 3px;
    width: 100%;
    min-width: 320px;
    font-variant-numeric: tabular-nums;
    table-layout: fixed;
  }
  th {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
    text-transform: capitalize;
    padding: 4px 6px;
  }
  thead th {
    text-align: center;
  }
  tbody th,
  tfoot th {
    text-align: right;
    width: 64px;
  }
  .corner {
    position: relative;
    width: 64px;
    font-size: 10.5px;
    color: var(--text-3);
    line-height: 1.2;
  }
  .axis-y,
  .axis-x {
    display: block;
    white-space: nowrap;
  }
  .axis-x {
    text-align: right;
  }
  .cell {
    position: relative;
    height: 54px;
    border-radius: 6px;
    text-align: center;
    vertical-align: middle;
    background: var(--surface-2);
    color: var(--text);
    line-height: 1.15;
    cursor: default;
  }
  .s1 {
    background: color-mix(in oklab, var(--heat) 14%, var(--surface));
  }
  .s2 {
    background: color-mix(in oklab, var(--heat) 30%, var(--surface));
  }
  .s3 {
    background: color-mix(in oklab, var(--heat) 48%, var(--surface));
  }
  .s4 {
    background: color-mix(in oklab, var(--heat) 70%, var(--surface));
    color: #fff;
  }
  .s5 {
    background: color-mix(in oklab, var(--heat) 92%, var(--surface));
    color: #fff;
  }
  @media (prefers-color-scheme: dark) {
    /* on the dark surface the low steps need more ink to separate from empty cells */
    .s1 {
      background: color-mix(in oklab, var(--heat) 24%, var(--surface));
    }
    .s2 {
      background: color-mix(in oklab, var(--heat) 38%, var(--surface));
    }
    .s3 {
      background: color-mix(in oklab, var(--heat) 54%, var(--surface));
    }
    .s4 {
      background: color-mix(in oklab, var(--heat) 68%, var(--surface));
    }
    .s5 {
      background: color-mix(in oklab, var(--heat) 82%, var(--surface));
    }
    .s4,
    .s5 {
      color: #fff;
    }
  }
  .s0 {
    color: var(--text-3);
  }
  .n {
    display: block;
    font-size: 16px;
    font-weight: 650;
  }
  .share {
    display: block;
    font-size: 11px;
    opacity: 0.85;
  }
  .diag {
    box-shadow: inset 0 0 0 2px var(--text);
  }
  .miss::after {
    content: '';
    position: absolute;
    top: 4px;
    right: 4px;
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 0 9px 9px 0;
    border-color: transparent var(--danger) transparent transparent;
  }
  .cell:hover {
    outline: 2px solid var(--surface);
    outline-offset: -4px;
  }
  td.total {
    text-align: center;
    font-size: 12.5px;
    color: var(--text-2);
  }
  th.total {
    color: var(--text-3);
    font-weight: 500;
  }
  thead th.total {
    color: var(--text-3);
    font-weight: 500;
    text-transform: none;
  }
  .readout {
    margin: 8px 0 0;
    font-size: 12.5px;
    color: var(--text-2);
    min-height: 1.6em;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .key {
    display: inline-block;
    width: 12px;
    height: 12px;
    border-radius: 3px;
    margin-left: 6px;
    position: relative;
    background: var(--surface-2);
  }
  .diag-key {
    box-shadow: inset 0 0 0 2px var(--text);
  }
  .miss-key::after {
    content: '';
    position: absolute;
    top: 0;
    right: 0;
    border-style: solid;
    border-width: 0 8px 8px 0;
    border-color: transparent var(--danger) transparent transparent;
  }
</style>
