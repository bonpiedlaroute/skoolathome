<script lang="ts">
  // Block 1: the lessons of the next school day, and the tests until D+3.
  import { dayTitle, hue, shortDay, testsSoon, when } from "../lib/home";
  import type { HomePage } from "../lib/types";

  let { page, today, next }: { page: HomePage; today: string; next: string | null } = $props();

  const slots = $derived(next ? page.slots.filter((s) => s.day === next) : []);
  const tests = $derived(testsSoon(page, today));
  const offBefore = $derived(next ? page.holidays.filter((d) => d > today && d < next).length : 0);
  const testOn = (code: string, day: string) => page.tests.some((t) => t.subject_code === code && t.day === day);
</script>

<section class="card block" aria-labelledby="upcoming-title">
  <div class="head">
    <span class="eyebrow">À venir</span>
    <h2 id="upcoming-title">{next ? dayTitle(next, today) : "Pas de cours prévu"}</h2>
    {#if next && dayTitle(next, today) === "Demain"}
      <span class="muted date">{new Date(`${next}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })}</span>
    {/if}
  </div>
  {#if offBefore > 0}
    <p class="hint small">Pas de cours avant : {offBefore === 1 ? "un jour de congé" : `${offBefore} jours de congé`}.</p>
  {/if}

  {#if slots.length}
    <ol class="timeline">
      {#each slots as slot (slot.starts + slot.subject_code)}
        <li class:cancelled={slot.cancelled} style="--h: {hue(slot.subject_code)}">
          <span class="time">{slot.starts}</span>
          <span class="dot" aria-hidden="true"></span>
          <span class="subject">{slot.subject}</span>
          {#if slot.cancelled}<span class="tag bad">annulé</span>{/if}
          {#if testOn(slot.subject_code, slot.day)}<span class="tag test">contrôle</span>{/if}
        </li>
      {/each}
    </ol>
  {:else if !next}
    <p class="muted">Aucun cours connu dans les deux semaines : vacances ?</p>
  {/if}

  <h3>Contrôles d'ici 3 jours</h3>
  {#if tests.length === 0}
    <p class="muted small">Aucun contrôle annoncé dans le cahier de texte d'ici 3 jours.</p>
  {:else}
    <ul class="tests">
      {#each tests as test (test.subject_code + test.day)}
        <li style="--h: {hue(test.subject_code)}">
          <span class="pill">{shortDay(test.day)}</span>
          <div class="what">
            <strong>{test.subject}</strong>
            <span class="when" class:urgent={test.day <= today || when(test.day, today) === "demain"}>{when(test.day, today)}</span>
            {#if test.what}<p class="muted small clamp">{test.what}</p>{/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .block { padding: 1.1rem 1.1rem 0.9rem; }
  .head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.2rem 0.6rem; margin-bottom: 0.6rem; }
  .eyebrow {
    width: 100%; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--accent);
  }
  h2 { margin: 0; font-size: 1.35rem; }
  .date { font-size: 0.95rem; }
  h3 { font-size: 0.95rem; margin: 1.1rem 0 0.5rem; color: var(--muted); font-weight: 600; }
  .small { font-size: 0.88rem; margin: 0.2rem 0; }
  .timeline { list-style: none; margin: 0; padding: 0; }
  .timeline li {
    display: flex; align-items: center; gap: 0.7rem; padding: 0.45rem 0; border-bottom: 1px dashed var(--line);
  }
  .timeline li:last-child { border-bottom: none; }
  .time { font-variant-numeric: tabular-nums; color: var(--muted); width: 3.2rem; font-size: 0.95rem; }
  .dot { width: 0.7rem; height: 0.7rem; border-radius: 50%; background: hsl(var(--h) 62% 50%); flex: none; }
  .subject { font-weight: 600; }
  .cancelled .subject { text-decoration: line-through; color: var(--muted); font-weight: 400; }
  .tag { font-size: 0.75rem; border-radius: 999px; padding: 0.1rem 0.55rem; font-weight: 600; }
  .tag.bad { background: var(--bad-bg); color: var(--bad); }
  .tag.test { background: var(--hint-bg); color: var(--ink); }
  .tests { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.55rem; }
  .tests li { display: flex; gap: 0.75rem; align-items: flex-start; }
  .pill {
    flex: none; min-width: 3.6rem; text-align: center; border-radius: 10px; padding: 0.35rem 0.4rem;
    background: hsl(var(--h) 62% 50% / 0.14); border-left: 4px solid hsl(var(--h) 62% 50%);
    font-weight: 700; font-size: 0.85rem; text-transform: capitalize;
  }
  .what { min-width: 0; }
  .when { font-size: 0.8rem; margin-left: 0.4rem; color: var(--muted); }
  .when.urgent { color: var(--bad); font-weight: 700; }
  .clamp { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>
