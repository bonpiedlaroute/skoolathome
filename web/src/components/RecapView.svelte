<script lang="ts">
  // The « cours en clair »: summary, solved example (solution behind a tap), points to remember.
  import { renderText } from "../lib/math";
  import type { Recap } from "../lib/types";

  let { recap, paper = false }: { recap: Recap; paper?: boolean } = $props();
  let showSolution = $state(false);
</script>

<section class="recap">
  <h2>Le cours en clair</h2>
  <p>{@html renderText(recap.summary)}</p>

  <h3>Un exemple</h3>
  <p>{@html renderText(recap.example.statement)}</p>
  {#if paper || showSolution}
    <div class="hint"><strong>Solution :</strong> {@html renderText(recap.example.solution)}</div>
  {:else}
    <p><button onclick={() => (showSolution = true)}>Voir la solution</button></p>
  {/if}

  <h3>À retenir</h3>
  <ul class="points">
    {#each recap.key_points as point}<li>{@html renderText(point)}</li>{/each}
  </ul>
</section>

<style>
  .recap h2 { margin-top: 0.4rem; }
  .recap h3 { margin: 1rem 0 0.3rem; font-size: 1.05rem; }
  .points li { margin: 0.35rem 0; font-weight: 600; }
</style>
