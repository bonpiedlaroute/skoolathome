<script lang="ts">
  // Block 3: the sheets to do before the next lessons and tests, from what the teachers wrote in
  // the notebook once the lesson was over. A sheet already made opens; else the parent asks for it.
  import { api } from "../lib/api";
  import { fileSize, frDay, hue, kindOf, when } from "../lib/home";
  import type { HomeLesson, HomeSheet, HomeSuggestion } from "../lib/types";

  let {
    suggestions,
    sheets,
    requested,
    today,
    role,
    onopen,
    documents = {},
  }: {
    suggestions: HomeSuggestion[];
    sheets: Record<string, HomeSheet>;
    requested: string[];
    today: string;
    role: "parent" | "child";
    onopen: (id: string) => void;
    documents?: Record<string, string>; // sha256 -> link, for the files online
  } = $props();

  let asked = $state<string[]>([]);
  let failed = $state("");

  const key = (s: HomeSuggestion) => `${s.subject_code}-${s.reason}-${s.for_day}`;
  const madeFor = (s: HomeSuggestion) => s.sheet_ids.map((id) => sheets[id]).filter(Boolean);
  const waiting = (s: HomeSuggestion) => asked.includes(key(s)) || requested.includes(s.subject_code);

  async function ask(s: HomeSuggestion) {
    failed = "";
    try {
      // A test coming: the complete preparation (45 min, to print too); otherwise a short sheet.
      await api.createRequest(s.subject_code, s.request.kind, s.request.focus, s.reason === "test" ? 45 : null);
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
    {#if (lesson.documents?.length ?? 0) > 0 || (lesson.links?.length ?? 0) > 0}
      <div class="files" aria-label="Pour aller plus loin">
        {#each lesson.documents ?? [] as file, i (file.sha256 ?? `${file.name}-${i}`)}
          {@const link = file.sha256 ? documents[file.sha256] : undefined}
          {#if link}
            <a class="file" href={link} target="_blank" rel="noopener" title="Ouvrir {file.name}">
              <span class="kind">{kindOf(file.name)}</span>
              <span class="label">{file.name}</span>
              {#if file.size}<span class="size">{fileSize(file.size)}</span>{/if}
            </a>
          {:else}
            <span class="file off" title="Pas encore récupéré : il est sur École Directe">
              <span class="kind">{kindOf(file.name)}</span>
              <span class="label">{file.name}</span>
              <span class="size">sur École Directe</span>
            </span>
          {/if}
        {/each}
        {#each lesson.links ?? [] as page (page.url)}
          <a class="file web" href={page.url} target="_blank" rel="noopener noreferrer" title={page.url}>
            <span class="kind">Lien</span>
            <span class="label">{page.label}</span>
            <span class="size" aria-hidden="true">↗</span>
          </a>
        {/each}
      </div>
    {:else if lesson.attachment}
      <div class="small doc">Le prof a joint son document à la séance.</div>
    {/if}
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
            <span class="muted small">Fiche demandée : l'ordinateur la prépare (environ 10 minutes s'il est allumé). Une notification préviendra.</span>
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
  .files { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }
  .file {
    display: inline-flex; align-items: center; gap: 0.45rem; max-width: 100%; min-height: 2.4rem;
    padding: 0.3rem 0.7rem 0.3rem 0.35rem; border: 1px solid var(--line); border-radius: 10px;
    color: var(--ink); text-decoration: none; background: var(--card); font-size: 0.85rem;
  }
  a.file:hover { border-color: var(--accent); }
  .file .kind {
    flex: none; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
    border-radius: 6px; padding: 0.2rem 0.4rem; background: hsl(var(--h) 62% 50% / 0.14);
  }
  .file.web .kind { background: var(--hint-bg); }
  .file .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .file .size { flex: none; color: var(--muted); font-size: 0.75rem; }
  .file.off { opacity: 0.65; border-style: dashed; }
  details { margin-top: 0.35rem; }
  summary { cursor: pointer; color: var(--accent); min-height: 2rem; display: list-item; }
  .actions { margin-top: 0.6rem; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
</style>
