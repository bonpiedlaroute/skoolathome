<script lang="ts">
  // The weekly review (T24): in two minutes, is the agent still right? Alerts first, then the
  // other indicators, then 5 sheets to annotate.
  import { onMount } from "svelte";

  import { api, ApiError } from "../lib/api";
  import type { WeeklyReview } from "../lib/types";
  import AnnotateSheet from "./AnnotateSheet.svelte";

  let { week, onexit }: { week: string; onexit: () => void } = $props();

  let review = $state<WeeklyReview | null>(null);
  let annotating = $state<string | null>(null);
  let error = $state("");

  const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { day: "numeric", month: "long" });

  async function load() {
    try {
      review = await api.review(week || "latest");
    } catch (problem) {
      error = problem instanceof ApiError && problem.status === 404
        ? "Pas encore de bilan : le premier arrive un vendredi soir."
        : "Impossible de charger le bilan.";
    }
  }

  onMount(load);
</script>

{#if annotating}
  <AnnotateSheet id={annotating} oncancel={() => (annotating = null)} onsaved={() => ((annotating = null), load())} />
{:else}
  <button class="link" onclick={onexit}>← Retour aux fiches</button>
  <h1>Bilan de la semaine</h1>
  {#if error}<p class="muted">{error}</p>{/if}
  {#if review}
    <p class="muted">Du {day(review.start)} au {day(review.end)}</p>
    <p>{review.body}</p>
    {#each review.indicators as indicator (indicator.name)}
      <div class="card" class:alert={indicator.alert}>
        <div class="row spread">
          <strong>{indicator.alert ? "⚠ " : ""}{indicator.label}</strong>
          <span class={indicator.alert ? "bad" : ""}>{indicator.value}</span>
        </div>
        {#if indicator.detail}<p class="muted" style="margin:0.4rem 0 0">{indicator.detail}</p>{/if}
      </div>
    {/each}

    {#if review.to_annotate.length}
      <h2>Fiches à noter</h2>
      <p class="muted">
        Pour chaque question : juste et utile, ou pas ? Tes notes sont comparées à celles du correcteur automatique.
      </p>
      {#each review.to_annotate as sheet (sheet.id)}
        {@const done = review.annotated.includes(sheet.id)}
        <div class="card row spread">
          <div><span class="muted">{sheet.subject}</span><br />{sheet.title}</div>
          <button class:primary={!done} onclick={() => (annotating = sheet.id)}>{done ? "Notée ✓" : "Noter"}</button>
        </div>
      {/each}
    {/if}
  {/if}
{/if}

<style>
  .alert { border: 2px solid var(--bad); background: var(--bad-bg); }
</style>
