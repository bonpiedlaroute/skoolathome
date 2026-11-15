<script lang="ts">
  // The sheet on paper: questions first, then the answers on a separate page.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { renderText } from "../lib/math";
  import { basedOn, formatLabel } from "../lib/sheet";
  import type { Item, Sheet } from "../lib/types";
  import RecapView from "./RecapView.svelte";

  let { id, onexit }: { id: string; onexit: () => void } = $props();

  let sheet = $state<Sheet | null>(null);
  let error = $state("");

  const answerText = (it: Item) => (Array.isArray(it.answer) ? it.answer.join(" ; ") : it.answer);
  const promptWithLines = (it: Item) => renderText(it.prompt).replaceAll("___", '<span class="blank-line"></span>');

  onMount(async () => {
    try {
      sheet = await api.sheet(id);
    } catch {
      error = "Impossible d'ouvrir cette fiche.";
    }
  });
</script>

<div class="row no-print">
  <button class="link" onclick={onexit}>← Retour</button>
  <button class="primary" disabled={!sheet} onclick={() => window.print()}>Imprimer</button>
</div>

{#if error}
  <p class="error">{error}</p>
{:else if sheet}
  <article class="paper">
    <header>
      <div class="muted">{sheet.subject} · {formatLabel(sheet.type)}</div>
      <h1>{sheet.title}</h1>
      {#if basedOn(sheet)}<p class="muted">{basedOn(sheet)}</p>{/if}
      <p>Prénom : ____________________ &nbsp;&nbsp; Date : ____________</p>
    </header>
    {#if sheet.recap}<RecapView recap={sheet.recap} paper />{/if}
    <ol>
      {#each sheet.items as it}
        <li class="question">
          <p>{@html it.type === "fill_blank" ? promptWithLines(it) : renderText(it.prompt)}</p>
          {#if it.type === "choice"}
            <ul class="boxes">{#each it.choices as choice}<li>☐ {@html renderText(choice)}</li>{/each}</ul>
          {:else if it.type === "matching"}
            <table>
              <tbody>
                {#each it.left as left, n}
                  <tr><td>{@html renderText(left)}</td><td>→ ____________</td><td class="muted">{it.choices[n] ?? ""}</td></tr>
                {/each}
              </tbody>
            </table>
          {:else if it.type === "open"}
            <div class="lines"></div>
          {:else if it.type !== "fill_blank"}
            <p>Réponse : ______________________________</p>
          {/if}
        </li>
      {/each}
    </ol>

    <section class="answers">
      <h2>Corrigé</h2>
      <ol>
        {#each sheet.items as it}
          <li>
            {#if it.type !== "open"}<strong>{@html renderText(answerText(it))}</strong>. {/if}
            {@html renderText(it.solution)}
            {#if it.parent_guide?.common_mistake}<div class="muted">Erreur fréquente : {@html renderText(it.parent_guide.common_mistake)}</div>{/if}
          </li>
        {/each}
      </ol>
    </section>
  </article>
{/if}

<style>
  .paper { background: var(--card); padding: 1rem 1.2rem; border-radius: 14px; margin-top: 1rem; }
  .question { margin-bottom: 1.4rem; break-inside: avoid; }
  .boxes { list-style: none; padding-left: 0.5rem; }
  .boxes li { margin: 0.3rem 0; }
  .lines { height: 7rem; background: repeating-linear-gradient(transparent 0 1.9rem, var(--line) 1.9rem 2rem); }
  :global(.blank-line) { display: inline-block; width: 6rem; border-bottom: 1px solid currentColor; }
  td { padding: 0.3rem 0.8rem 0.3rem 0; }
  .answers { break-before: page; margin-top: 2rem; }
  @media print {
    .paper { padding: 0; border-radius: 0; margin: 0; }
  }
</style>
