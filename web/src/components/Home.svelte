<script lang="ts">
  import { onMount } from "svelte";

  import { api, warmCache } from "../lib/api";
  import { outbox } from "../lib/outbox.svelte";
  import { formatLabel } from "../lib/sheet";
  import type { Device, SheetSummary } from "../lib/types";
  import Notifications from "./Notifications.svelte";

  let {
    device,
    onopen,
    ondevices,
    onrequests,
    oninstructions,
    onreview,
  }: {
    device: Device;
    onopen: (id: string, how?: "review") => void;
    ondevices: () => void;
    onrequests: () => void;
    oninstructions: () => void;
    onreview: () => void;
  } = $props();

  let sheets = $state<SheetSummary[] | null>(null);
  let error = $state("");
  let offline = $state(!navigator.onLine);

  const day = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });

  function status(sheet: SheetSummary): string {
    if (sheet.answered === 0) return `${sheet.items} questions · environ ${sheet.estimated_minutes} min`;
    if (sheet.answered < sheet.items) return `En cours : ${sheet.answered}/${sheet.items}`;
    return `Terminée : ${sheet.correct}/${sheet.items}`;
  }

  onMount(() => {
    const update = () => (offline = !navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    (async () => {
      try {
        sheets = await api.sheets();
      } catch {
        error = navigator.onLine
          ? "Impossible de charger les fiches."
          : "Pas de connexion, et les fiches n'ont pas encore été chargées sur cet appareil.";
        return;
      }
      if (navigator.serviceWorker?.controller) void warmCache();
    })();
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  });
</script>

<div class="row spread">
  <h1>Tes fiches</h1>
  {#if device.role === "parent"}
    <div class="row">
      <button class="link" onclick={onrequests}>Demander une fiche</button>
      <button class="link" onclick={oninstructions}>Consignes</button>
      <button class="link" onclick={onreview}>Bilan</button>
      <button class="link" onclick={ondevices}>Appareils</button>
    </div>
  {/if}
</div>

{#if offline || outbox.pending > 0}
  <p class="hint" role="status">
    {offline ? "Pas de connexion : tu peux quand même faire les fiches déjà chargées." : ""}
    {outbox.pending > 0 ? ` ${outbox.pending} réponse${outbox.pending > 1 ? "s" : ""} en attente d'envoi.` : ""}
  </p>
{/if}

{#if error}
  <p class="error">{error}</p>
{:else if sheets === null}
  <p class="muted">Chargement…</p>
{:else if sheets.length === 0}
  <div class="card"><p>Aucune fiche pour l'instant. Reviens ce soir !</p></div>
{:else}
  {#each sheets as sheet (sheet.id)}
    <div class="card">
      <div class="muted">{sheet.subject} · {formatLabel(sheet.type)} · {day(sheet.visible_from)}</div>
      <h2 style="margin:0.3rem 0">{sheet.title}</h2>
      <div class="row spread">
        <span class={sheet.answered >= sheet.items ? "good" : "muted"}>{status(sheet)}</span>
        <button class="primary" onclick={() => onopen(sheet.id)}>
          {sheet.answered === 0 ? "Commencer" : sheet.answered < sheet.items ? "Reprendre" : "Ouvrir"}
        </button>
      </div>
      {#if sheet.to_review > 0}
        <div class="row spread" style="margin-top:0.6rem">
          <span>{sheet.to_review} point{sheet.to_review > 1 ? "s" : ""} à voir ensemble</span>
          <button onclick={() => onopen(sheet.id, "review")}>Voir ensemble</button>
        </div>
      {/if}
    </div>
  {/each}
{/if}

<Notifications />
