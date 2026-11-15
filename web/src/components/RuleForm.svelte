<script lang="ts">
  // The rule form (T21): « le formulaire fait foi ». Saved by the laptop as it is, without the model.
  import { FORMATS, fromForm, newRuleId, SCOPES, toForm, TRIGGERS } from "../lib/rules";
  import { SUBJECTS } from "../lib/sheet";
  import type { RuleBody } from "../lib/types";

  let {
    initial,
    title,
    fixedId = false,
    onsave,
    oncancel,
  }: { initial: RuleBody | null; title: string; fixedId?: boolean; onsave: (rule: RuleBody) => void; oncancel: () => void } =
    $props();

  // The form starts from the rule once, then the parent edits it.
  // svelte-ignore state_referenced_locally
  let form = $state(toForm(initial));
  // svelte-ignore state_referenced_locally
  const isNew = !initial;
  const limit = $derived(form.trigger === "load_limit");

  function save(event: Event) {
    event.preventDefault();
    if (isNew && !form.id.trim()) form.id = newRuleId(form);
    onsave(fromForm($state.snapshot(form), initial));
  }
</script>

<form class="card" onsubmit={save}>
  <h2 style="margin-top:0">{title}</h2>

  <label for="trigger">Quand</label>
  <select id="trigger" bind:value={form.trigger}>
    {#each TRIGGERS as [code, name]}<option value={code}>{name}</option>{/each}
  </select>

  {#if limit}
    <label for="max">Minutes de fiches au plus, par soir</label>
    <input id="max" type="number" min="1" max="180" bind:value={form.max_minutes} />
  {:else}
    <label for="subject">Matière</label>
    <select id="subject" bind:value={form.subject}>
      <option value="">Toutes les matières</option>
      {#each SUBJECTS as [code, name]}<option value={code}>{name}</option>{/each}
    </select>

    {#if form.trigger === "before_test"}
      <label for="offsets">Combien de jours avant le contrôle</label>
      <input id="offsets" type="text" bind:value={form.offsets} placeholder="4, 2, 1" />
    {:else}
      <label for="scope">Sur quoi</label>
      <select id="scope" bind:value={form.scope}>
        {#each SCOPES as [code, name]}<option value={code}>{name}</option>{/each}
      </select>
      {#if form.scope === "last_n_lessons"}
        <label for="n">Combien de cours</label>
        <input id="n" type="number" min="1" max="10" bind:value={form.n_lessons} />
      {/if}
    {/if}

    <label for="format">Fiche</label>
    <select id="format" bind:value={form.format}>
      {#each FORMATS as [code, name]}<option value={code}>{name}</option>{/each}
    </select>

    {#if form.format === "exercises" || form.format === "test_revision"}
      <label for="items">Nombre de questions</label>
      <select id="items" bind:value={form.items}>
        <option value={null}>selon la durée</option>
        {#each [3, 4, 5, 6, 8, 10] as n}<option value={n}>{n}</option>{/each}
      </select>
    {/if}

    <label for="minutes">Durée visée (minutes)</label>
    <input id="minutes" type="number" min="1" max="60" bind:value={form.target_minutes} />

    <label class="choice"><input type="checkbox" bind:checked={form.oral} /> Comme une interrogation orale</label>
    <label class="choice"><input type="checkbox" bind:checked={form.together} /> À faire ensemble</label>
    {#if form.subject}
      <label class="choice"><input type="checkbox" bind:checked={form.replaces_default} /> Remplace l'exercice du soir dans cette matière</label>
    {/if}
  {/if}

  <label class="choice"><input type="checkbox" bind:checked={form.active} /> Règle active</label>

  {#if isNew && !fixedId}
    <label for="rule-id">Nom de la règle <span class="muted">(facultatif)</span></label>
    <input id="rule-id" type="text" bind:value={form.id} maxlength="40" placeholder={newRuleId(form)} />
  {/if}

  <div class="row" style="margin-top:1rem">
    <button class="primary">Enregistrer</button>
    <button type="button" onclick={oncancel}>Annuler</button>
  </div>
</form>

<style>
  label { display: block; margin: 0.8rem 0 0.3rem; font-weight: 600; }
  input[type="number"] { font: inherit; width: 8rem; min-height: 3rem; padding: 0.6rem 0.8rem; border-radius: 12px;
    border: 1px solid var(--line); background: var(--card); color: var(--ink); }
  .choice { font-weight: 400; display: flex; gap: 0.5rem; align-items: center; min-height: 2.6rem; margin: 0.2rem 0; }
</style>
