<script lang="ts">
  // The answer area of one question. `value` is what will be corrected:
  // a text, or a list of texts for blanks and matching.
  import { renderText } from "../lib/math";
  import type { Item } from "../lib/types";
  import Keypad from "./Keypad.svelte";

  let {
    item,
    value = $bindable(),
    disabled = false,
    reveal = false,
  }: { item: Item; value: string | string[]; disabled?: boolean; reveal?: boolean } = $props();

  const parts = $derived(item.prompt.split("___"));

  /** A choice written "3/6" is displayed as a real fraction. */
  function choiceLabel(choice: string): string {
    const fraction = /^(-?\d+)\/(\d+)$/.exec(choice.trim());
    return renderText(fraction ? `$\\dfrac{${fraction[1]}}{${fraction[2]}}$` : choice);
  }
</script>

{#if item.type === "choice"}
  <div class="choices">
    {#each item.choices as choice}
      <button
        type="button"
        class:selected={value === choice}
        class:right={reveal && choice === item.answer}
        {disabled}
        onclick={() => (value = choice)}
      >
        {@html choiceLabel(choice)}
      </button>
    {/each}
  </div>
{:else if item.type === "number" || item.type === "fraction"}
  <input
    type="text"
    inputmode="none"
    class="number"
    bind:value={value as string}
    {disabled}
    aria-label="Ta réponse"
    placeholder={item.type === "fraction" ? "par exemple 3/4" : ""}
  />
  {#if !disabled}<Keypad bind:value={value as string} fraction={item.type === "fraction"} />{/if}
{:else if item.type === "short_answer"}
  <input type="text" bind:value={value as string} {disabled} autocomplete="off" autocapitalize="off" aria-label="Ta réponse" />
{:else if item.type === "fill_blank"}
  <p class="blanks">
    {#each parts as part, index}
      {@html renderText(part)}{#if index < parts.length - 1}
        <input
          type="text"
          class="blank"
          bind:value={(value as string[])[index]}
          {disabled}
          autocomplete="off"
          autocapitalize="off"
          aria-label={`Trou numéro ${index + 1}`}
        />
      {/if}
    {/each}
  </p>
{:else if item.type === "matching"}
  {#each item.left as left, index}
    <div class="pair">
      <span>{@html renderText(left)}</span>
      <select bind:value={(value as string[])[index]} {disabled} aria-label={`Associer ${left}`}>
        <option value="">Choisir…</option>
        {#each item.choices as choice}<option value={choice}>{choice}</option>{/each}
      </select>
    </div>
  {/each}
{:else}
  <textarea bind:value={value as string} {disabled} placeholder="Écris ta réponse, ou dis-la à voix haute." aria-label="Ta réponse"></textarea>
{/if}

<style>
  .choices { display: grid; gap: 0.6rem; }
  .choices button { text-align: left; min-height: 3.4rem; font-size: 1.1rem; }
  .choices button:disabled { opacity: 1; }
  .choices button.right { border: 2px solid var(--good); background: var(--good-bg); }
  .choices button.selected { border: 2px solid var(--accent); background: color-mix(in srgb, var(--accent) 14%, var(--card)); }
  .number { font-size: 1.5rem; max-width: 20rem; }
  .blanks { line-height: 3.4rem; }
  .blank { display: inline-block; width: 7rem; margin: 0 0.3rem; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem; align-items: center; margin: 0.5rem 0; }
</style>
