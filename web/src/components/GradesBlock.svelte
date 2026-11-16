<script lang="ts">
  // Block 2: the general average against the class's, then the latest grade of each subject
  // against the class (its range, its average), and the subject's average over the period.
  import { difference, frDay, gap, gradeBar, hue, number } from "../lib/home";
  import type { HomeAverages, HomeSubjectGrades } from "../lib/types";

  let { subjects, averages = null }: { subjects: HomeSubjectGrades[]; averages?: HomeAverages | null } = $props();

  const general = $derived(averages && averages.average !== null ? averages : null);
  const generalGap = $derived(general ? difference(general.average, general.class_average) : null);

  let all = $state(false);
  const SHOWN = 6;
  const listed = $derived(all ? subjects : subjects.slice(0, SHOWN));
  const scale = (out_of: number | null) => (out_of ? `/${number(out_of)}` : "");
</script>

<section class="card block" aria-labelledby="grades-title">
  <div class="head">
    <span class="eyebrow">Notes</span>
    <h2 id="grades-title">Dernières notes</h2>
  </div>
  {#if general}
    <div class="general">
      <div class="stat">
        <span class="label">Moyenne générale</span>
        <strong>{number(general.average)}</strong>
      </div>
      <div class="stat">
        <span class="label">Classe</span>
        <strong class="theirs">{number(general.class_average)}</strong>
      </div>
      {#if generalGap}
        <span class="gap big" class:above={generalGap.above} title="Écart avec la moyenne générale de la classe">{generalGap.text}</span>
      {/if}
      <p class="muted small how">
        {general.period ? `${general.period.charAt(0).toUpperCase()}${general.period.slice(1)} : ` : ""}les moyennes
        des {general.subjects} matières additionnées puis divisées par {general.subjects}. Chaque note compte sur 20, avec son coefficient.
      </p>
    </div>
  {/if}
  {#if subjects.length === 0}
    <p class="muted">Pas encore de note ce trimestre.</p>
  {:else}
    <div class="grid">
      {#each listed as subject (subject.subject_code)}
        {@const grade = subject.grades[0]}
        {@const bar = gradeBar(grade)}
        {@const versus = gap(grade)}
        <article class="grade" style="--h: {hue(subject.subject_code)}">
          <header>
            <span class="dot" aria-hidden="true"></span>
            <span class="name">{subject.subject}</span>
          </header>
          <div class="value">
            <strong>{grade.value}</strong><span class="muted">{scale(grade.out_of)}</span>
            {#if versus}
              <span class="gap" class:above={versus.above} title="Écart avec la moyenne de la classe">{versus.text}</span>
            {/if}
          </div>
          <div class="muted small">{grade.kind} · {frDay(grade.day)}</div>
          {#if grade.title}<div class="small title">{grade.title}</div>{/if}
          {#if bar}
            <div class="bar" role="img"
              aria-label="Note {grade.value}{scale(grade.out_of)}, classe : moyenne {number(grade.class_average)}, de {number(grade.class_min)} à {number(grade.class_max)}">
              <span class="range" style="left: {bar.low}%; width: {Math.max(bar.high - bar.low, 1)}%"></span>
              {#if bar.average !== null}<span class="average" style="left: {bar.average}%"></span>{/if}
              <span class="score" style="left: {bar.score}%"></span>
            </div>
          {/if}
          {#if grade.class_average !== null}
            <dl class="class">
              <div><dt>moy. classe</dt><dd>{number(grade.class_average)}</dd></div>
              <div><dt>min</dt><dd>{number(grade.class_min)}</dd></div>
              <div><dt>max</dt><dd>{number(grade.class_max)}</dd></div>
            </dl>
          {/if}
          {#if subject.average !== null && subject.average !== undefined}
            {@const meanGap = difference(subject.average, subject.class_average)}
            <div class="means">
              <span class="muted small">Moyenne</span>
              <strong>{number(subject.average)}</strong>
              {#if subject.class_average !== null && subject.class_average !== undefined}
                <span class="muted small">classe {number(subject.class_average)}</span>
              {/if}
              {#if meanGap}<span class="gap" class:above={meanGap.above}>{meanGap.text}</span>{/if}
            </div>
          {/if}
          {#if subject.grades.length > 1}
            <div class="before small muted">
              Avant :
              {#each subject.grades.slice(1) as earlier, i (earlier.day + i)}
                <span class="chip" title="{earlier.kind}, {frDay(earlier.day)}">{earlier.value}{scale(earlier.out_of)}</span>
              {/each}
            </div>
          {/if}
        </article>
      {/each}
    </div>
    {#if subjects.length > SHOWN}
      <button class="link" onclick={() => (all = !all)}>
        {all ? "Moins de matières" : `Toutes les matières (${subjects.length})`}
      </button>
    {/if}
  {/if}
</section>

<style>
  .block { padding: 1.1rem 1.1rem 0.9rem; }
  .head { margin-bottom: 0.7rem; }
  .general {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 1.4rem; margin-bottom: 0.9rem;
    padding: 0.75rem 0.9rem; border-radius: 12px; background: var(--hint-bg);
  }
  .stat { display: grid; }
  .stat .label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
  .stat strong { font-size: 2rem; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .stat .theirs { color: var(--muted); }
  .gap.big { margin-left: 0; font-size: 0.95rem; padding: 0.2rem 0.65rem; }
  .how { flex-basis: 100%; margin: 0; }
  .means {
    display: flex; align-items: baseline; gap: 0.4rem; flex-wrap: wrap;
    border-top: 1px dashed var(--line); padding-top: 0.4rem; margin-top: 0.2rem;
  }
  .means strong { font-variant-numeric: tabular-nums; }
  .means .gap { margin-left: auto; }
  .eyebrow { display: block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
  h2 { margin: 0; font-size: 1.35rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(12.5rem, 1fr)); gap: 0.7rem; }
  .grade {
    border: 1px solid var(--line); border-radius: 12px; padding: 0.7rem 0.8rem 0.75rem;
    border-top: 4px solid hsl(var(--h) 62% 50%); display: grid; gap: 0.25rem; align-content: start;
  }
  header { display: flex; align-items: center; gap: 0.45rem; }
  .dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; background: hsl(var(--h) 62% 50%); }
  .name { font-weight: 600; font-size: 0.92rem; }
  .value { display: flex; align-items: baseline; gap: 0.15rem; }
  .value strong { font-size: 1.7rem; font-variant-numeric: tabular-nums; }
  .gap {
    margin-left: auto; font-size: 0.8rem; font-weight: 700; border-radius: 999px; padding: 0.1rem 0.5rem;
    background: var(--hint-bg); color: var(--ink);
  }
  .gap.above { background: var(--good-bg); color: var(--good); }
  .small { font-size: 0.82rem; }
  .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar { position: relative; height: 0.55rem; border-radius: 999px; background: var(--line); margin: 0.6rem 0.5rem 0.35rem; }
  .range { position: absolute; top: 0; bottom: 0; border-radius: 999px; background: hsl(var(--h) 62% 50% / 0.35); }
  .average { position: absolute; top: -0.2rem; bottom: -0.2rem; width: 2px; margin-left: -1px; background: var(--ink); opacity: 0.7; }
  .score {
    position: absolute; top: 50%; width: 0.95rem; height: 0.95rem; margin: -0.475rem 0 0 -0.475rem; border-radius: 50%;
    background: hsl(var(--h) 62% 45%); border: 2px solid var(--card); box-shadow: 0 0 0 1px hsl(var(--h) 62% 45%);
  }
  .class { display: grid; grid-template-columns: 1.6fr 1fr 1fr; gap: 0.3rem; margin: 0.15rem 0 0; }
  .class dt { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .class dd { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; font-size: 0.92rem; }
  .before { margin-top: 0.15rem; display: flex; gap: 0.3rem; align-items: center; flex-wrap: wrap; }
  .chip { border: 1px solid var(--line); border-radius: 999px; padding: 0 0.45rem; color: var(--ink); }
</style>
