<script lang="ts">
  // Opened from the « explication prête » notification (T29): the question, then its explanation.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { renderText } from "../lib/math";
  import type { Explanation, Sheet } from "../lib/types";
  import ExplainBox from "./ExplainBox.svelte";

  let { id, onexit, onsheet }: { id: string; onexit: () => void; onsheet: (sheetId: string) => void } = $props();

  let explanation = $state<Explanation | null>(null);
  let sheet = $state<Sheet | null>(null);
  let error = $state("");
  const item = $derived(sheet && explanation ? sheet.items.find((it) => it.id === explanation!.item_id) : null);

  onMount(async () => {
    try {
      explanation = await api.explanation(id);
      sheet = await api.sheet(explanation.sheet_id);
    } catch {
      error = "Impossible de charger l'explication.";
    }
  });
</script>

<button class="link" onclick={onexit}>← Fiches</button>
{#if error}<p class="error">{error}</p>{/if}
{#if explanation && sheet}
  <div class="muted">{sheet.subject} · {sheet.title}</div>
  <div class="card">
    {#if item}<p class="prompt">{@html renderText(item.prompt)}</p>{/if}
    {#if explanation.question}<p class="muted">Ta question : « {explanation.question} »</p>{/if}
    <ExplainBox sheetId={sheet.id} itemId={explanation.item_id} {explanation} onchange={(e) => (explanation = e)} />
  </div>
  <p><button onclick={() => onsheet(sheet!.id)}>Ouvrir la fiche</button></p>
{:else if !error}
  <p class="muted">Chargement…</p>
{/if}

<style>
  .prompt { font-size: 1.2rem; }
</style>
