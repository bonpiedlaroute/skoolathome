<script lang="ts">
  // One sheet, two ways to do it, switchable at any time without losing an answer.
  //   Solo: corrected at once in the browser, a second try, hints on demand.
  //   Together: answer and parent guide behind a tap, the parent rates ✓ ~ ✗.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import { check } from "../lib/check";
  import { renderText } from "../lib/math";
  import { MAX_SOLO_TRIES, noProgress, soloDone, toReview, type Counters } from "../lib/progress";
  import { basedOn, formatLabel, quoteSource } from "../lib/sheet";
  import type { Item, Rating, Sheet } from "../lib/types";
  import ItemInput from "./ItemInput.svelte";
  import RecapView from "./RecapView.svelte";
  import ReportDialog from "./ReportDialog.svelte";

  let {
    id,
    start = "choose",
    onexit,
    onprint,
  }: { id: string; start?: "choose" | "together" | "review"; onexit: () => void; onprint: () => void } = $props();

  type Mode = "solo" | "together";
  type Screen = "loading" | "choose" | "item" | "solo-end" | "together-end";

  let sheet = $state<Sheet | null>(null);
  let progress = $state<Record<string, Counters>>({});
  let error = $state("");
  let screen = $state<Screen>("loading");
  let mode = $state<Mode>("solo");
  let reviewOnly = $state<string[] | null>(null); // together, only these items
  let index = $state(0);

  // The current question
  let value = $state<string | string[]>("");
  let hints = $state(0);
  let soloState = $state<"answer" | "wrong" | "done">("answer");
  let lastCorrect = $state(false);
  let lastWrongChoice = $state("");
  let openRevealed = $state(false);
  let typedVerdict = $state<boolean | null>(null);
  let showAnswer = $state(false);
  let showGuide = $state(false);
  let reporting = $state(false);
  let thanks = $state(false);

  const item = $derived(sheet?.items[index] ?? null);
  const counters = (it: Item): Counters => progress[it.id] ?? noProgress();
  const reviewIds = $derived(sheet ? sheet.items.filter((it) => toReview(counters(it), it.type)).map((it) => it.id) : []);
  const doneCount = $derived(sheet ? sheet.items.filter((it) => soloDone(counters(it), it.type) || counters(it).rating).length : 0);
  const score = $derived(sheet ? sheet.items.filter((it) => counters(it).solo_right > 0).length : 0);
  const order = $derived(
    sheet ? (reviewOnly ? sheet.items.filter((it) => reviewOnly!.includes(it.id)) : sheet.items).map((it) => sheet!.items.indexOf(it)) : [],
  );
  const position = $derived(order.indexOf(index));

  const blank = (it: Item): string | string[] =>
    it.type === "fill_blank" ? it.prompt.split("___").slice(1).map(() => "") : it.type === "matching" ? it.left.map(() => "") : "";
  const isEmpty = (answer: string | string[]) =>
    Array.isArray(answer) ? answer.some((part) => part.trim() === "") : answer.trim() === "";
  const answerText = (it: Item) => (Array.isArray(it.answer) ? it.answer.join(" ; ") : it.answer);

  function show(position: number) {
    index = position;
    value = sheet ? blank(sheet.items[position]) : "";
    hints = 0;
    soloState = "answer";
    openRevealed = false;
    typedVerdict = null;
    showAnswer = false;
    showGuide = false;
    window.scrollTo(0, 0);
  }

  /** Solo: the next question not done yet, starting after `from`. */
  function nextSolo(from: number) {
    if (!sheet) return;
    const n = sheet.items.length;
    for (let step = 1; step <= n; step++) {
      const candidate = (from + step) % n;
      if (!soloDone(counters(sheet.items[candidate]), sheet.items[candidate].type)) return show(candidate);
    }
    finishSolo();
  }

  function finishSolo() {
    screen = "solo-end";
    api.finish(id); // tells the parent, once
  }

  function begin(chosen: Mode, onlyReview = false) {
    if (!sheet) return;
    mode = chosen;
    reviewOnly = onlyReview && reviewIds.length > 0 ? [...reviewIds] : null;
    screen = "item";
    if (chosen === "solo") {
      nextSolo(sheet.items.length - 1); // from the start
    } else {
      show(order[0] ?? 0);
    }
  }

  function switchMode(next: Mode) {
    if (next === mode || !sheet) return;
    reviewOnly = null;
    if (next === "solo") {
      mode = "solo";
      if (soloDone(counters(sheet.items[index]), sheet.items[index].type)) nextSolo(index);
      else show(index);
    } else {
      mode = "together";
      show(index);
    }
  }

  function bump(it: Item, change: Partial<Counters>) {
    progress[it.id] = { ...counters(it), ...change };
  }

  // ------------------------------------------------------------------ solo

  function validate() {
    if (!item) return;
    const response = $state.snapshot(value) as string | string[];
    const correct = check(item.check.mode, item.answer, response, { variants: item.variants, tolerance: item.check.tolerance });
    api.attempt(id, item.id, { mode: "solo", response, hints_used: hints }, sheet?.trace_id);
    const c = counters(item);
    bump(item, correct ? { solo_right: c.solo_right + 1 } : { solo_wrong: c.solo_wrong + 1 });
    lastCorrect = correct;
    lastWrongChoice = correct ? "" : typeof response === "string" ? response : "";
    soloState = correct || counters(item).solo_wrong >= MAX_SOLO_TRIES ? "done" : "wrong";
  }

  function retry() {
    if (!item) return;
    if (item.type === "choice") value = "";
    soloState = "answer";
  }

  function selfAssess(right: boolean) {
    if (!item) return;
    api.attempt(id, item.id, { mode: "solo", response: right, hints_used: hints }, sheet?.trace_id);
    const c = counters(item);
    bump(item, right ? { solo_right: c.solo_right + 1 } : { solo_wrong: c.solo_wrong + 1 });
    lastCorrect = right;
    soloState = "done";
  }

  // -------------------------------------------------------------- together

  function verifyTyped() {
    if (!item) return;
    typedVerdict = check(item.check.mode, item.answer, $state.snapshot(value) as string | string[], {
      variants: item.variants,
      tolerance: item.check.tolerance,
    });
  }

  function rate(rating: Rating) {
    if (!item) return;
    const typed = $state.snapshot(value) as string | string[];
    api.attempt(
      id, item.id, { mode: "together", rating, ...(isEmpty(typed) || item.type === "open" ? {} : { response: typed }) },
      sheet?.trace_id,
    );
    bump(item, { rating });
    move(1);
  }

  function move(step: number) {
    const next = position + step;
    if (next >= order.length) screen = "together-end";
    else if (next >= 0) show(order[next]);
  }

  onMount(async () => {
    try {
      sheet = await api.sheet(id);
    } catch {
      error = "Impossible d'ouvrir cette fiche. Elle n'a pas encore été chargée sur cet appareil ?";
      return;
    }
    try {
      progress = await api.progress(id);
    } catch {
      progress = {}; // offline and never loaded: start from scratch, answers are still kept
    }
    if (start === "review") begin("together", true);
    else if (start === "together") begin("together");
    else screen = "choose";
  });
</script>

<div class="row spread no-print">
  <button class="link" onclick={onexit}>← Fiches</button>
  {#if screen === "item"}
    <div class="toggle" role="group" aria-label="Mode">
      <button class:on={mode === "solo"} onclick={() => switchMode("solo")}>Seul</button>
      <button class:on={mode === "together"} onclick={() => switchMode("together")}>Ensemble</button>
    </div>
  {/if}
</div>

{#if error}
  <p class="error">{error}</p>
{:else if !sheet || screen === "loading"}
  <p class="muted">Chargement…</p>
{:else if screen === "choose"}
  <div class="card">
    <div class="muted">{sheet.subject} · {formatLabel(sheet.type)}</div>
    <h1 style="margin-top:0.3rem">{sheet.title}</h1>
    <p class="muted">
      {sheet.items.length} questions · environ {sheet.estimated_minutes} min{doneCount ? ` · ${doneCount} déjà faite${doneCount > 1 ? "s" : ""}` : ""}
    </p>
    {#if basedOn(sheet)}<p class="muted">{basedOn(sheet)}</p>{/if}
    {#if sheet.recap}
      <RecapView recap={sheet.recap} />
      <h2>Les questions</h2>
    {/if}
    <div class="modes">
      <button class="primary big" onclick={() => begin("solo")}>Seul<small>correction tout de suite</small></button>
      <button class="big" onclick={() => begin("together")}>Ensemble<small>avec un parent</small></button>
    </div>
    {#if reviewIds.length}
      <p><button onclick={() => begin("together", true)}>Voir ensemble les {reviewIds.length} point{reviewIds.length > 1 ? "s" : ""} à revoir</button></p>
    {/if}
    <p><button class="link" onclick={onprint}>Imprimer pour travailler sur papier</button></p>
  </div>
{:else if screen === "solo-end"}
  <div class="card">
    <h1>Fiche terminée</h1>
    <p style="font-size:1.6rem"><strong>{score} / {sheet.items.length}</strong></p>
    {#if reviewIds.length === 0}
      <p>Bravo, il n'y a rien à revoir !</p>
    {:else}
      <p>À voir ensemble avec un parent :</p>
      <ul>
        {#each sheet.items.filter((it) => reviewIds.includes(it.id)) as it}
          <li>Question {sheet.items.indexOf(it) + 1}{it.concept ? ` · ${it.concept}` : ""}</li>
        {/each}
      </ul>
      <p class="muted">Un parent reçoit une notification avec un lien direct vers ces points.</p>
      <p><button onclick={() => begin("together", true)}>Les voir ensemble maintenant</button></p>
    {/if}
    <button class="primary" onclick={onexit}>Retour aux fiches</button>
  </div>
{:else if screen === "together-end"}
  <div class="card">
    <h1>C'est fini pour aujourd'hui</h1>
    <p>{sheet.items.filter((it) => counters(it).rating === "good").length} / {order.length} réussies ensemble.</p>
    <button class="primary" onclick={onexit}>Retour aux fiches</button>
  </div>
{:else if item}
  <div class="muted">{sheet.subject} · {sheet.title}</div>
  <progress value={position} max={order.length} aria-label="Avancement"></progress>
  {#if sheet.recap}
    <details class="no-print"><summary>Revoir le cours en clair</summary><RecapView recap={sheet.recap} /></details>
  {/if}
  <div class="card">
    <div class="row spread">
      <span class="muted">Question {position + 1} sur {order.length}{reviewOnly ? " à revoir" : ""}</span>
      <button class="link no-print" onclick={() => (reporting = true)}>Signaler</button>
    </div>
    {#if thanks}<p class="good" role="status">Merci, c'est noté.</p>{/if}
    {#if item.type !== "fill_blank"}<p class="prompt">{@html renderText(item.prompt)}</p>{/if}

    {#if mode === "solo"}
      <ItemInput {item} bind:value disabled={soloState !== "answer" || openRevealed} reveal={soloState === "done"} />

      {#each item.hints.slice(0, hints) as hint}
        <div class="hint">💡 {@html renderText(hint)}</div>
      {/each}

      {#if item.type === "open" && soloState === "answer"}
        {#if !openRevealed}
          <p><button class="primary" onclick={() => (openRevealed = true)}>Voir le corrigé</button></p>
        {:else}
          <div class="hint"><strong>Corrigé :</strong> {@html renderText(item.solution)}</div>
          <p>Ta réponse disait-elle la même chose ?</p>
          <div class="row">
            <button class="primary" onclick={() => selfAssess(true)}>Oui, j'avais bon</button>
            <button onclick={() => selfAssess(false)}>Pas tout à fait</button>
          </div>
        {/if}
      {:else if soloState === "answer"}
        <div class="row" style="margin-top:1rem">
          <button class="primary" disabled={isEmpty(value)} onclick={validate}>Valider</button>
          {#if hints < item.hints.length}
            <button onclick={() => (hints += 1)}>Un indice ({item.hints.length - hints})</button>
          {/if}
        </div>
      {:else if soloState === "wrong"}
        <div class="feedback bad" role="status">
          <strong>✗ Pas encore.</strong>
          {#if item.why_wrong[lastWrongChoice]}<p>{@html renderText(item.why_wrong[lastWrongChoice])}</p>{/if}
          <p>Tu as droit à un deuxième essai.</p>
        </div>
        <div class="row">
          <button class="primary" onclick={retry}>Réessayer</button>
          {#if hints < item.hints.length}
            <button onclick={() => ((hints += 1), retry())}>Réessayer avec un indice</button>
          {/if}
        </div>
      {:else}
        <div class="feedback {lastCorrect ? 'good' : 'bad'}" role="status">
          <strong>{lastCorrect ? "✓ Juste !" : item.type === "open" ? "D'accord." : "✗ Pas cette fois."}</strong>
          {#if !lastCorrect && item.type !== "open"}
            {#if item.why_wrong[lastWrongChoice]}<p>{@html renderText(item.why_wrong[lastWrongChoice])}</p>{/if}
            <p><strong>Réponse :</strong> {@html renderText(answerText(item))}</p>
          {/if}
          {#if item.solution && item.type !== "open"}<p>{@html renderText(item.solution)}</p>{/if}
          {#if item.quote}<p class="quote">📖 Dans {quoteSource(sheet, item)} : « {@html renderText(item.quote.excerpt)} »</p>{/if}
          {#if toReview(counters(item), item.type)}<p>On la reverra ensemble.</p>{/if}
        </div>
        <button class="primary" onclick={() => nextSolo(index)}>Continuer</button>
      {/if}
    {:else}
      <!-- Together: nothing is revealed without a tap. -->
      <ItemInput {item} bind:value reveal={showAnswer} />
      {#if typedVerdict !== null}
        <div class="feedback {typedVerdict ? 'good' : 'bad'}">{typedVerdict ? "✓ La réponse tapée est juste." : "✗ La réponse tapée n'est pas celle attendue."}</div>
      {/if}
      <div class="row" style="margin-top:1rem">
        {#if item.type !== "open" && !isEmpty(value)}<button onclick={verifyTyped}>Vérifier la réponse tapée</button>{/if}
        <button onclick={() => (showAnswer = !showAnswer)}>{showAnswer ? "Cacher la réponse" : "Afficher la réponse"}</button>
        {#if item.parent_guide}
          <button onclick={() => (showGuide = !showGuide)}>{showGuide ? "Cacher le guide" : "Guide du parent"}</button>
        {/if}
      </div>
      {#if showAnswer}
        <div class="hint">
          {#if item.type !== "open"}<p><strong>Réponse :</strong> {@html renderText(answerText(item))}</p>{/if}
          {#if item.solution}<p>{@html renderText(item.solution)}</p>{/if}
          {#if item.quote}<p class="quote">📖 Dans {quoteSource(sheet, item)} : « {@html renderText(item.quote.excerpt)} »</p>{/if}
        </div>
      {/if}
      {#if showGuide && item.parent_guide}
        <div class="guide">
          {#if item.parent_guide.explanation}<p><strong>À expliquer :</strong> {@html renderText(item.parent_guide.explanation)}</p>{/if}
          {#if item.parent_guide.common_mistake}<p><strong>Erreur fréquente :</strong> {@html renderText(item.parent_guide.common_mistake)}</p>{/if}
          {#if item.parent_guide.oral_followup}<p><strong>Pour aller plus loin à l'oral :</strong> {@html renderText(item.parent_guide.oral_followup)}</p>{/if}
        </div>
      {/if}
      <p class="muted" style="margin-bottom:0.3rem">Comment ça s'est passé ?</p>
      <div class="ratings">
        <button class="rate good" onclick={() => rate("good")} aria-label="Réussi">✓</button>
        <button class="rate partial" onclick={() => rate("partial")} aria-label="À moitié">~</button>
        <button class="rate bad" onclick={() => rate("bad")} aria-label="Pas réussi">✗</button>
      </div>
      <div class="row spread" style="margin-top:0.8rem">
        <button class="link" disabled={position === 0} onclick={() => move(-1)}>← Précédente</button>
        <button class="link" onclick={() => move(1)}>Passer →</button>
      </div>
    {/if}
  </div>
{/if}

{#if reporting && item}
  <ReportDialog
    sheetId={id}
    itemId={item.id}
    traceId={sheet?.trace_id}
    onclose={(sent) => {
      reporting = false;
      thanks = sent;
      if (sent) setTimeout(() => (thanks = false), 3000);
    }}
  />
{/if}

<style>
  .prompt { font-size: 1.2rem; }
  progress { width: 100%; height: 0.6rem; margin: 0.5rem 0; accent-color: var(--accent); }
  .toggle { display: inline-flex; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  .toggle button { border: none; border-radius: 0; min-height: 2.6rem; }
  .toggle button.on { background: var(--accent); color: var(--accent-ink); font-weight: 600; }
  .modes { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; margin: 1rem 0; }
  .big { min-height: 6rem; font-size: 1.3rem; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.3rem; }
  .big small { font-size: 0.85rem; font-weight: 400; opacity: 0.85; }
  .ratings { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; }
  .rate { min-height: 4rem; font-size: 1.8rem; font-weight: 700; }
  .rate.good { color: var(--good); border-color: var(--good); }
  .rate.partial { color: #b7791f; border-color: #b7791f; }
  .rate.bad { color: var(--bad); border-color: var(--bad); }
  .guide { border-left: 4px solid var(--accent); padding: 0.2rem 0 0.2rem 0.9rem; margin: 0.8rem 0; }
  .quote { font-style: italic; opacity: 0.85; }
  details { margin: 0.4rem 0 0.8rem; }
  summary { cursor: pointer; min-height: 2.4rem; display: flex; align-items: center; }
</style>
