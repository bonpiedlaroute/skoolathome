<script lang="ts">
  // Parent view: ask the agent for a sheet, and see what became of the requests.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { SUBJECTS, subjectName } from "../lib/sheet";
  import type { RequestKind, SheetRequest } from "../lib/types";

  let { onexit, onopen }: { onexit: () => void; onopen: (id: string) => void } = $props();

  let requests = $state<SheetRequest[]>([]);
  let subject = $state("MATHS");
  let kind = $state<RequestKind>("revision");
  let focus = $state("");
  let items = $state<number | null>(null);
  let sent = $state(false);
  let error = $state("");

  const when = (iso: string) =>
    new Date(iso).toLocaleString("fr-FR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  async function load() {
    try {
      requests = await api.requests();
    } catch {
      error = "Impossible de charger les demandes.";
    }
  }

  async function ask(event: Event) {
    event.preventDefault();
    error = "";
    try {
      await api.createRequest(subject, kind, focus.trim(), items);
      sent = true;
      focus = "";
      await load();
    } catch {
      error = "Impossible d'envoyer la demande. Vérifie la connexion.";
    }
  }

  onMount(load);
</script>

<button class="link" onclick={onexit}>← Retour aux fiches</button>
<h1>Demander une fiche</h1>

<form class="card" onsubmit={ask}>
  <label for="subject">Matière</label>
  <select id="subject" bind:value={subject}>
    {#each SUBJECTS as [code, name]}<option value={code}>{name}</option>{/each}
  </select>

  <fieldset>
    <legend>Quoi ?</legend>
    <label class="choice"><input type="radio" bind:group={kind} value="revision" /> Une révision sur un point précis</label>
    <label class="choice"><input type="radio" bind:group={kind} value="extra" /> Un exercice en plus</label>
  </fieldset>

  <label for="focus">Sur quoi ? <span class="muted">(facultatif)</span></label>
  <input id="focus" type="text" bind:value={focus} maxlength="300"
    placeholder="par exemple : les fractions égales, le chapitre sur les volcans…" />

  <label for="items">Nombre de questions</label>
  <select id="items" bind:value={items}>
    <option value={null}>selon le cours</option>
    {#each [3, 4, 5, 6, 8, 10] as n}<option value={n}>{n}</option>{/each}
  </select>

  <p><button class="primary">Demander</button></p>
  {#if sent}
    <p class="good" role="status">
      C'est noté. Le portable la fera à son prochain passage (le soir, ou tout de suite avec
      <code>skoolathome evening --only-requests</code>), en relisant tout le cours de la matière.
    </p>
  {/if}
  {#if error}<p class="error">{error}</p>{/if}
</form>

{#if requests.length}
  <h2>Demandes récentes</h2>
  {#each requests as request (request.id)}
    <div class="card">
      <div class="muted">{subjectName(request.subject)} · {request.kind === "revision" ? "révision" : "exercice en plus"} · {when(request.created_at)}</div>
      {#if request.focus}<p style="margin:0.3rem 0">« {request.focus} »</p>{/if}
      {#if request.status === "pending"}
        <p class="muted">En attente du portable.</p>
      {:else if request.status === "done" && request.sheet_id}
        <div class="row spread">
          <span class="good">Prête</span>
          <button class="primary" onclick={() => onopen(request.sheet_id!)}>Ouvrir</button>
        </div>
      {:else}
        <p class="bad">Pas possible : {request.reason}</p>
      {/if}
    </div>
  {/each}
{/if}

<style>
  label { display: block; margin: 0.8rem 0 0.3rem; font-weight: 600; }
  select, input[type="text"] { width: 100%; }
  fieldset { border: none; padding: 0; margin: 0.8rem 0 0; }
  legend { font-weight: 600; margin-bottom: 0.3rem; }
  .choice { font-weight: 400; display: flex; gap: 0.5rem; align-items: center; min-height: 2.6rem; margin: 0; }
</style>
