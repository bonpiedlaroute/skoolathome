<script lang="ts">
  // The weekly annotation (T24): for each question, is it right and useful? Your verdicts are
  // compared with the judge's, question by question: that is the agreement in the review.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { renderText } from "../lib/math";
  import { quoteSource } from "../lib/sheet";
  import type { ItemAnnotation, Problem, Sheet } from "../lib/types";

  let { id, onsaved, oncancel }: { id: string; onsaved: () => void; oncancel: () => void } = $props();

  const PROBLEMS: [Problem, string][] = [
    ["answer_correct", "La réponse attendue est fausse"],
    ["faithful", "Ce n'est pas ce que dit le cours"],
    ["level_ok", "Pas clair, ou pas du niveau 5e"],
    ["useful", "Inutile (organisation de la classe…)"],
  ];

  let sheet = $state<Sheet | null>(null);
  let verdicts = $state<Record<string, ItemAnnotation>>({});
  let error = $state("");
  let saving = $state(false);

  const answer = (value: string | string[]) => (Array.isArray(value) ? value.join(" ; ") : value);
  const complete = $derived(
    !!sheet && sheet.items.every((it) => verdicts[it.id] && (verdicts[it.id].ok || verdicts[it.id].problem)),
  );

  function judge(itemId: string, ok: boolean) {
    verdicts[itemId] = { item_id: itemId, ok, problem: ok ? null : (verdicts[itemId]?.problem ?? null), comment: verdicts[itemId]?.comment ?? "" };
  }

  async function save() {
    if (!sheet || !complete) return;
    saving = true;
    error = "";
    try {
      await api.annotate(sheet.id, sheet.items.map((it) => $state.snapshot(verdicts[it.id])));
      onsaved();
    } catch {
      error = "Impossible d'enregistrer. Vérifie la connexion.";
    } finally {
      saving = false;
    }
  }

  onMount(async () => {
    try {
      sheet = await api.sheet(id);
    } catch {
      error = "Impossible de charger la fiche.";
    }
  });
</script>

<button class="link" onclick={oncancel}>← Retour au bilan</button>
{#if error}<p class="error">{error}</p>{/if}
{#if sheet}
  <h1>{sheet.title}</h1>
  <p class="muted">{sheet.subject} · pour chaque question : juste et utile, ou pas ?</p>
  {#each sheet.items as item (item.id)}
    <div class="card">
      <div class="muted">{item.id}</div>
      <p style="margin:0.3rem 0"><strong>{@html renderText(item.prompt)}</strong></p>
      {#if item.choices.length}<p class="muted">Choix : {item.choices.join(" · ")}</p>{/if}
      <p>Réponse attendue : {@html renderText(answer(item.answer))}</p>
      {#if item.quote}<p class="muted">« {item.quote.excerpt} » ({quoteSource(sheet, item)})</p>{/if}
      <div class="row">
        <button class:primary={verdicts[item.id]?.ok === true} onclick={() => judge(item.id, true)}>✓ Juste et utile</button>
        <button class:primary={verdicts[item.id]?.ok === false} onclick={() => judge(item.id, false)}>✗ Problème</button>
      </div>
      {#if verdicts[item.id]?.ok === false}
        <select style="margin-top:0.6rem" bind:value={verdicts[item.id].problem} aria-label="Le problème">
          <option value={null}>Quel problème ?</option>
          {#each PROBLEMS as [code, label]}<option value={code}>{label}</option>{/each}
        </select>
        <input type="text" style="margin-top:0.5rem" bind:value={verdicts[item.id].comment} maxlength="300"
          placeholder="Un détail ? (facultatif)" aria-label="Commentaire" />
      {/if}
    </div>
  {/each}
  <p><button class="primary" disabled={!complete || saving} onclick={save}>Enregistrer mes notes</button></p>
{:else if !error}
  <p class="muted">Chargement…</p>
{/if}
