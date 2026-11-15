<script lang="ts">
  // Parent view (T21): an instruction in plain French; the agent asks if it must, says what it
  // understood, and saves the rule only after [Valider]. The rules, and the form to change them.
  import { onMount } from "svelte";

  import { api, ApiError } from "../lib/api";
  import type { Instruction, PublishedRule, RuleBody } from "../lib/types";
  import RuleForm from "./RuleForm.svelte";

  let { onexit }: { onexit: () => void } = $props();

  let instructions = $state<Instruction[]>([]);
  let rules = $state<PublishedRule[]>([]);
  let text = $state("");
  let answers = $state<Record<string, string>>({});
  let refusals = $state<Record<string, string>>({}); // why the parent refuses a proposal of the agent (T28)
  let error = $state("");
  // What the form edits: a proposal ([Modifier]), an existing rule, or a new one.
  let editing = $state<{ title: string; rule: RuleBody | null; instruction?: string } | null>(null);

  const when = (iso: string) =>
    new Date(iso).toLocaleString("fr-FR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  async function load() {
    try {
      [instructions, rules] = await Promise.all([api.instructions(), api.rules()]);
    } catch {
      error = "Impossible de charger les consignes.";
    }
  }

  async function act(action: () => Promise<unknown>) {
    error = "";
    try {
      await action();
      editing = null;
      await load();
    } catch (problem) {
      error = problem instanceof ApiError && problem.status === 422
        ? `Règle refusée : ${problem.message}`
        : "Impossible d'envoyer. Vérifie la connexion.";
    }
  }

  function send(event: Event) {
    event.preventDefault();
    if (!text.trim()) return;
    void act(async () => {
      await api.createInstruction({ text: text.trim() });
      text = "";
    });
  }

  function answer(instruction: Instruction) {
    const written = (answers[instruction.id] ?? "").trim();
    if (written) void act(() => api.replyInstruction(instruction.id, "answer", { text: written }));
  }

  function saveForm(rule: RuleBody) {
    const target = editing;
    if (!target) return;
    void act(() => (target.instruction
      ? api.replyInstruction(target.instruction, "edit", { rule })
      : api.createInstruction({ rule })));
  }

  onMount(load);
</script>

<button class="link" onclick={onexit}>← Retour aux fiches</button>
<h1>Consignes</h1>
{#if error}<p class="error" role="alert">{error}</p>{/if}

{#if editing}
  <RuleForm initial={editing.rule} title={editing.title} fixedId={!!editing.instruction}
    onsave={saveForm} oncancel={() => (editing = null)} />
{:else}
  <form class="card" onsubmit={send}>
    <label for="instruction">Ta consigne, avec tes mots</label>
    <textarea id="instruction" bind:value={text} maxlength="600"
      placeholder="Par exemple : le prof d'histoire interroge à chaque cours sur le cours précédent, et c'est noté."></textarea>
    <p><button class="primary" disabled={!text.trim()}>Envoyer</button></p>
    <p class="muted">
      Le portable la lit à son prochain passage. Il te dit ce qu'il a compris, et n'enregistre rien sans ton accord.
    </p>
  </form>

  {#each instructions as instruction (instruction.id)}
    <div class="card">
      <div class="muted">{when(instruction.created_at)}{instruction.origin === "agent" ? " · proposition de l'agent" : ""}</div>
      {#if instruction.origin === "agent"}
        <p style="margin:0.3rem 0">{instruction.text}</p>
        {#if instruction.facts?.length}
          <ul class="facts">{#each instruction.facts as fact}<li>{fact}</li>{/each}</ul>
        {/if}
      {:else}
        <p style="margin:0.3rem 0">{instruction.text ? `« ${instruction.text} »` : "Règle écrite avec le formulaire"}</p>
      {/if}
      {#if instruction.waiting || instruction.status === "pending"}
        <p class="muted">{instruction.waiting ? "Réponse envoyée, " : ""}en attente du portable.</p>
      {:else if instruction.status === "question"}
        <p><strong>Question :</strong> {instruction.question}</p>
        <div class="row">
          <input type="text" style="flex:1" bind:value={answers[instruction.id]} maxlength="600" aria-label="Ta réponse" />
          <button class="primary" onclick={() => answer(instruction)}>Répondre</button>
        </div>
      {:else if instruction.status === "proposed"}
        <p>{instruction.summary}</p>
        <div class="row">
          <button class="primary" onclick={() => act(() => api.replyInstruction(instruction.id, "approve"))}>
            {instruction.origin === "agent" ? "Accepter" : "Valider"}
          </button>
          <button onclick={() => (editing = { title: "Modifier la règle proposée", rule: instruction.proposal, instruction: instruction.id })}>
            Modifier
          </button>
          <button onclick={() => act(() => api.replyInstruction(instruction.id, "reject", { text: (refusals[instruction.id] ?? "").trim() }))}>
            {instruction.origin === "agent" ? "Refuser" : "Annuler"}
          </button>
        </div>
        {#if instruction.origin === "agent"}
          <input type="text" style="margin-top:0.5rem" bind:value={refusals[instruction.id]} maxlength="600"
            placeholder="Pourquoi refuser ? (facultatif) Elle ne sera plus jamais proposée." aria-label="Motif du refus" />
        {/if}
        <p class="muted" style="margin-bottom:0.3rem">Ou corrige avec tes mots :</p>
        <div class="row">
          <input type="text" style="flex:1" bind:value={answers[instruction.id]} maxlength="600"
            placeholder="non, c'est la prof d'anglais" aria-label="Ta correction" />
          <button onclick={() => answer(instruction)}>Envoyer</button>
        </div>
      {:else if instruction.status === "saved"}
        <p class="good">{instruction.summary}</p>
      {:else if instruction.status === "rejected"}
        <p class="muted">{instruction.origin === "agent" ? "Refusée : elle ne sera plus proposée." : "Annulée : aucune règle enregistrée."}</p>
      {:else}
        <p class="bad">Pas compris : {instruction.reason} </p>
      {/if}
    </div>
  {/each}

  <h2>Règles de l'agent</h2>
  {#if rules.length === 0}
    <p class="muted">La liste arrive avec la prochaine publication du portable.</p>
  {/if}
  {#each rules as rule (rule.id)}
    <div class="card">
      <p style="margin-top:0" class={rule.status === "active" ? "" : "muted"}>{rule.summary}</p>
      <div class="row spread">
        <span class="muted">{rule.id} · version {rule.version}</span>
        <button onclick={() => (editing = { title: "Modifier la règle", rule: rule.body })}>Modifier</button>
      </div>
    </div>
  {/each}
  <p><button onclick={() => (editing = { title: "Nouvelle règle", rule: null })}>Nouvelle règle, avec le formulaire</button></p>
{/if}

<style>
  label { display: block; margin: 0 0 0.4rem; font-weight: 600; }
  .facts { margin: 0.3rem 0 0.6rem; padding-left: 1.2rem; color: var(--muted); }
</style>
