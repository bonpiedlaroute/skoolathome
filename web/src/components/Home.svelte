<script lang="ts">
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import type { Device, SheetSummary } from "../lib/types";

  let { device, onopen, ondevices }: { device: Device; onopen: (id: string) => void; ondevices: () => void } =
    $props();

  let sheets = $state<SheetSummary[] | null>(null);
  let error = $state("");

  const day = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });

  function status(sheet: SheetSummary): string {
    if (sheet.answered === 0) return `${sheet.items} questions · environ ${sheet.estimated_minutes} min`;
    if (sheet.answered < sheet.items) return `En cours : ${sheet.answered}/${sheet.items}`;
    return `Terminée : ${sheet.correct}/${sheet.items}`;
  }

  onMount(async () => {
    try {
      sheets = await api.sheets();
    } catch {
      error = "Impossible de charger les fiches.";
    }
  });
</script>

<div class="row spread">
  <h1>Tes fiches</h1>
  {#if device.role === "parent"}<button class="link" onclick={ondevices}>Appareils</button>{/if}
</div>

{#if error}
  <p class="error">{error}</p>
{:else if sheets === null}
  <p class="muted">Chargement…</p>
{:else if sheets.length === 0}
  <div class="card"><p>Aucune fiche pour l'instant. Reviens ce soir !</p></div>
{:else}
  {#each sheets as sheet (sheet.id)}
    <div class="card">
      <div class="muted">{sheet.subject} · {day(sheet.visible_from)}</div>
      <h2 style="margin:0.3rem 0">{sheet.title}</h2>
      <div class="row spread">
        <span class={sheet.answered >= sheet.items ? "good" : "muted"}>{status(sheet)}</span>
        <button class="primary" onclick={() => onopen(sheet.id)}>
          {sheet.answered === 0 ? "Commencer" : sheet.answered < sheet.items ? "Reprendre" : "Refaire"}
        </button>
      </div>
    </div>
  {/each}
{/if}
