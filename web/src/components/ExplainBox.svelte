<script lang="ts">
  // « Je n'ai pas compris » (T29): ask for an explanation of this question, see it when the
  // computer has written it, and say whether it helped.
  import { api } from "../lib/api";
  import { renderText } from "../lib/math";
  import type { Explanation } from "../lib/types";

  let {
    sheetId,
    itemId,
    explanation = null,
    onchange,
  }: { sheetId: string; itemId: string; explanation?: Explanation | null; onchange: (e: Explanation) => void } =
    $props();

  let asking = $state(false);
  let question = $state("");
  let error = $state("");

  async function ask() {
    error = "";
    try {
      onchange(await api.explain(sheetId, itemId, question.trim()));
      asking = false;
    } catch {
      error = "Pas de connexion : redemande quand le réseau revient.";
    }
  }

  async function rate(helped: boolean) {
    if (!explanation) return;
    try {
      onchange(await api.helped(explanation.id, helped));
    } catch {
      error = "Pas de connexion : réessaie plus tard.";
    }
  }
</script>

<div class="explain no-print">
  {#if !explanation}
    {#if !asking}
      <button class="link" onclick={() => (asking = true)}>Je n'ai pas compris</button>
    {:else}
      <label for="explain-{itemId}">Qu'est-ce qui n'est pas clair ? <span class="muted">(facultatif)</span></label>
      <input id="explain-{itemId}" type="text" bind:value={question} maxlength="300"
        placeholder="par exemple : je ne vois pas d'où vient le 6" />
      <div class="row" style="margin-top:0.5rem">
        <button class="primary" onclick={ask}>Demander une explication</button>
        <button onclick={() => (asking = false)}>Annuler</button>
      </div>
    {/if}
  {:else if explanation.status === "pending"}
    <p class="hint" role="status">Demande envoyée. Réponse dès que l'ordinateur est allumé.</p>
  {:else if explanation.status === "abstained"}
    <p class="hint">Pas d'explication possible : {explanation.reason}</p>
  {:else if explanation.explanation}
    <div class="hint">
      <p><strong>Explication :</strong> {@html renderText(explanation.explanation.text)}</p>
      {#if explanation.explanation.example}<p><strong>Exemple :</strong> {@html renderText(explanation.explanation.example)}</p>{/if}
      {#if explanation.explanation.quote}
        <p class="quote">📖 Dans {explanation.explanation.source_label || "ton cours"} : « {@html renderText(explanation.explanation.quote.excerpt)} »</p>
      {/if}
    </div>
    {#if explanation.helped === null}
      <p style="margin-bottom:0.3rem">Ça t'a aidé ?</p>
      <div class="row">
        <button class="primary" onclick={() => rate(true)}>Oui</button>
        <button onclick={() => rate(false)}>Toujours pas</button>
      </div>
    {:else}
      <p class="muted">{explanation.helped ? "Super !" : "C'est noté : vois-la avec un parent, et la prochaine explication sera meilleure."}</p>
    {/if}
  {/if}
  {#if error}<p class="error">{error}</p>{/if}
</div>

<style>
  .explain { margin-top: 0.6rem; }
  label { display: block; margin: 0.5rem 0 0.3rem; font-weight: 600; }
  .quote { font-style: italic; opacity: 0.85; }
</style>
