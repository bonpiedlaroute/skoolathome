<script lang="ts">
  // Solo mode: one question at a time, corrected at once in the browser.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { check } from "../lib/check";
  import { renderText } from "../lib/math";
  import type { Item, Response, Sheet } from "../lib/types";
  import ItemInput from "./ItemInput.svelte";

  let { id, onexit }: { id: string; onexit: () => void } = $props();

  let sheet = $state<Sheet | null>(null);
  let error = $state("");
  let index = $state(0);
  let value = $state<string | string[]>("");
  let verdict = $state<boolean | null>(null); // null: not answered yet
  let revealed = $state(false); // open question: the model answer is shown
  let hints = $state(0);
  let results = $state<boolean[]>([]);
  let unsent = $state(0);

  const item = $derived(sheet?.items[index] ?? null);
  const finished = $derived(sheet !== null && index >= sheet.items.length);

  const blank = (question: Item): string | string[] =>
    question.type === "fill_blank"
      ? question.prompt.split("___").slice(1).map(() => "")
      : question.type === "matching"
        ? question.left.map(() => "")
        : "";

  const isEmpty = (answer: string | string[]) =>
    Array.isArray(answer) ? answer.some((part) => part.trim() === "") : answer.trim() === "";

  function show(position: number) {
    index = position;
    verdict = null;
    revealed = false;
    hints = 0;
    const next = sheet?.items[position];
    value = next ? blank(next) : "";
  }

  async function record(question: Item, response: Response, correct: boolean) {
    verdict = correct;
    results = [...results, correct];
    try {
      await api.attempt(sheet!.id, question.id, response, hints);
    } catch {
      unsent += 1; // the answer is still corrected on screen; offline sending comes in T10
    }
  }

  function validate() {
    if (!item) return;
    const response = $state.snapshot(value) as string | string[];
    const correct = check(item.check.mode, item.answer, response, {
      variants: item.variants,
      tolerance: item.check.tolerance,
    });
    record(item, response, correct);
  }

  onMount(async () => {
    try {
      sheet = await api.sheet(id);
      show(0);
    } catch {
      error = "Impossible d'ouvrir cette fiche.";
    }
  });
</script>

<button class="link" onclick={onexit}>← Retour aux fiches</button>

{#if error}
  <p class="error">{error}</p>
{:else if !sheet}
  <p class="muted">Chargement…</p>
{:else if finished}
  {@const score = results.filter(Boolean).length}
  <div class="card">
    <h1>Fiche terminée</h1>
    <p style="font-size:1.6rem"><strong>{score} / {sheet.items.length}</strong></p>
    <p>{score === sheet.items.length ? "Bravo, tout est juste !" : "Les questions ratées sont à revoir avec un parent."}</p>
    {#if unsent > 0}<p class="muted">{unsent} réponse(s) n'ont pas pu être envoyées (pas de connexion).</p>{/if}
    <button class="primary" onclick={onexit}>Retour aux fiches</button>
  </div>
{:else if item}
  <div class="muted">{sheet.subject} · {sheet.title}</div>
  <progress value={index} max={sheet.items.length} aria-label="Avancement"></progress>
  <div class="card">
    <div class="muted">Question {index + 1} sur {sheet.items.length}</div>
    {#if item.type !== "fill_blank"}<p class="prompt">{@html renderText(item.prompt)}</p>{/if}

    <ItemInput {item} bind:value disabled={verdict !== null || revealed} reveal={verdict !== null} />

    {#each item.hints.slice(0, hints) as hint}
      <div class="hint">💡 {@html renderText(hint)}</div>
    {/each}

    {#if verdict === null && !revealed}
      <div class="row" style="margin-top:1rem">
        {#if item.type === "open"}
          <button class="primary" onclick={() => (revealed = true)}>Voir le corrigé</button>
        {:else}
          <button class="primary" disabled={isEmpty(value)} onclick={validate}>Valider</button>
        {/if}
        {#if hints < item.hints.length}
          <button onclick={() => (hints += 1)}>Un indice ({item.hints.length - hints})</button>
        {/if}
      </div>
    {:else if verdict === null}
      <!-- Open question: the student compares with the model answer. -->
      <div class="feedback" style="background:var(--hint-bg)">
        <strong>Corrigé :</strong> {@html renderText(item.solution)}
      </div>
      <p>Ta réponse disait-elle la même chose ?</p>
      <div class="row">
        <button class="primary" onclick={() => record(item, true, true)}>Oui, j'avais bon</button>
        <button onclick={() => record(item, false, false)}>Pas tout à fait</button>
      </div>
    {:else}
      <div class="feedback {verdict ? 'good' : 'bad'}" role="status">
        <strong>{verdict ? "✓ Juste !" : "✗ Pas encore."}</strong>
        {#if !verdict && typeof value === "string" && item.why_wrong[value]}
          <p>{@html renderText(item.why_wrong[value])}</p>
        {/if}
        {#if !verdict && item.type !== "open"}
          <p>
            <strong>Réponse :</strong>
            {@html renderText(Array.isArray(item.answer) ? item.answer.join(" ; ") : item.answer)}
          </p>
        {/if}
        {#if item.solution && item.type !== "open"}<p>{@html renderText(item.solution)}</p>{/if}
      </div>
      <button class="primary" onclick={() => show(index + 1)}>
        {index + 1 < sheet.items.length ? "Question suivante" : "Voir mon résultat"}
      </button>
    {/if}
  </div>
{/if}

<style>
  .prompt { font-size: 1.2rem; }
  progress { width: 100%; height: 0.6rem; margin: 0.5rem 0; accent-color: var(--accent); }
</style>
