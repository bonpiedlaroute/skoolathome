<script lang="ts">
  // On-screen pad for numbers: digits, minus sign, decimal comma and, for fractions, the bar.
  let { value = $bindable(""), fraction = false }: { value: string; fraction?: boolean } = $props();

  const keys = $derived(["7", "8", "9", "4", "5", "6", "1", "2", "3", "−", "0", ",", ...(fraction ? ["/"] : [])]);

  function press(key: string) {
    if (key === "−") value = value.startsWith("-") ? value.slice(1) : `-${value}`;
    else value += key;
  }
</script>

<div class="pad">
  {#each keys as key}
    <button type="button" onclick={() => press(key)} aria-label={key === "−" ? "signe moins" : key}>{key}</button>
  {/each}
  <button type="button" class="wide" onclick={() => (value = value.slice(0, -1))} aria-label="effacer">⌫</button>
</div>

<style>
  .pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; max-width: 20rem; margin-top: 0.75rem; }
  .pad button { min-height: 3.6rem; font-size: 1.4rem; }
  .wide { grid-column: span 3; }
  .pad:has(button:nth-child(14)) .wide { grid-column: span 2; }
</style>
