<script lang="ts">
  // Block 3: the sheets to do before the next lessons and tests, from what the teachers wrote in
  // the notebook once the lesson was over. A sheet already made opens; else the parent asks for it.
  import { api } from "../lib/api";
  import { frDay, hue, when } from "../lib/home";
  import type { HomeLesson, HomeSheet, HomeSuggestion } from "../lib/types";

  let {
    suggestions,
    sheets,
    requested,
    today,
    role,
    onopen,
  }: {
    suggestions: HomeSuggestion[];
    sheets: Record<string, HomeSheet>;
    requested: string[];
    today: string;
    role: "parent" | "child";
    onopen: (id: string) => void;
  } = $props();

  let asked = $state<string[]>([]);
  let failed = $state("");

  const key = (s: HomeSuggestion) => `${s.subject_code}-${s.reason}-${s.for_day}`;
  const madeFor = (s: HomeSuggestion) => s.sheet_ids.map((id) => sheets[id]).filter(Boolean);
  const waiting = (s: HomeSuggestion) => asked.includes(key(s)) || requested.includes(s.subject_code);

  async function ask(s: HomeSuggestion) {
    failed = "";
    try {
      await api.createRequest(s.subject_code, s.request.kind, s.request.focus, null);
      asked = [...asked, key(s)];
    } catch {
      failed = "Impossible d'envoyer la demande. Vérifie la connexion.";
    }
  }
</script>

{#snippet lessonOf(lesson: HomeLesson)}
  <div class="lesson">
    <div class="muted small">D'après le cours du {frDay(lesson.day)}</div>
    {#if lesson.points.length}
      <ul>{#each lesson.points as point}<li>{point}</li>{/each}</ul>
    {/if}
    {#if lesson.attachment}<div class="small doc">Le prof a joint son document à la séance.</div>{/if}
  </div>
{/snippet}

<section class="card block" aria-labelledby="suggestions-title">
  <div class="head">
    <span class="eyebrow">Révisions</span>
    <h2 id="suggestions-title">Fiches suggérées</h2>
  </div>
  {#if suggestions.length === 0}
    <p class="muted">Rien à réviser en particulier : pas de contrôle proche, et le cahier de texte est vide pour les prochains cours.</p>
  {/if}
  {#each suggestions as s (key(s))}
    {@const made = madeFor(s)}
    <article class="suggestion" class:test={s.reason === "test"} style="--h: {hue(s.subject_code)}">
      <div class="top">
        <span class="badge">{s.reason === "test" ? `Contrôle ${when(s.for_day, today)}` : `Cours ${when(s.for_day, today)}`}</span>
        <strong>{s.subject}</strong>
      </div>
      {#if s.lessons.length}
        {@render lessonOf(s.lessons[0])}
      {/if}
      {#if s.lessons.length > 1}
        <details>
          <summary class="small">{s.lessons.length - 1} séance{s.lessons.length > 2 ? "s" : ""} d'avant</summary>
          {#each s.lessons.slice(1) as lesson (lesson.day)}{@render lessonOf(lesson)}{/each}
        </details>
      {/if}
      <div class="actions">
        {#each made as sheet (sheet.id)}
          <button class="primary" onclick={() => onopen(sheet.id)}>
            {sheet.answered === 0 ? "Faire la fiche" : sheet.answered < sheet.items ? `Reprendre (${sheet.answered}/${sheet.items})` : `Revoir (${sheet.correct}/${sheet.items})`}
          </button>
        {:else}
          {#if waiting(s)}
            <span class="muted small">Fiche demandée : prête au prochain passage de l'ordinateur.</span>
          {:else if role === "parent"}
            <button onclick={() => ask(s)}>Demander cette fiche</button>
          {:else}
            <span class="muted small">Pas encore de fiche : relis les points ci-dessus.</span>
          {/if}
        {/each}
      </div>
    </article>
  {/each}
  {#if failed}<p class="error">{failed}</p>{/if}
</section>

<style>
  .block { padding: 1.1rem 1.1rem 0.9rem; }
  .head { margin-bottom: 0.4rem; }
  .eyebrow { display: block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
  h2 { margin: 0; font-size: 1.35rem; }
  .suggestion { border-left: 4px solid hsl(var(--h) 62% 50%); padding: 0.6rem 0 0.7rem 0.85rem; margin-top: 0.8rem; }
  .suggestion + .suggestion { border-top: 1px solid var(--line); }
  .top { display: flex; align-items: center; gap: 0.55rem; flex-wrap: wrap; }
  .badge {
    font-size: 0.75rem; font-weight: 700; border-radius: 999px; padding: 0.15rem 0.6rem;
    background: hsl(var(--h) 62% 50% / 0.14); color: var(--ink);
  }
  .test .badge { background: var(--bad-bg); color: var(--bad); }
  .lesson { margin-top: 0.45rem; }
  .small { font-size: 0.85rem; }
  ul { margin: 0.25rem 0 0; padding-left: 1.2rem; }
  li { margin: 0.15rem 0; line-height: 1.4; }
  .doc { margin-top: 0.3rem; color: var(--accent); }
  details { margin-top: 0.35rem; }
  summary { cursor: pointer; color: var(--accent); min-height: 2rem; display: list-item; }
  .actions { margin-top: 0.6rem; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
</style>
