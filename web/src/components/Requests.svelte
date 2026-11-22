<script lang="ts">
  // Parent view: ask the agent for a sheet, with photos of the notebook if need be, check the text
  // read on them, and see what became of the requests.
  import { onMount, tick } from "svelte";

  import { api } from "../lib/api";
  import { MAX_PHOTOS, shrink } from "../lib/photos";
  import { DURATIONS, SUBJECTS, subjectName } from "../lib/sheet";
  import type { RequestKind, SheetRequest } from "../lib/types";

  let { onexit, onopen }: { onexit: () => void; onopen: (id: string) => void } = $props();

  let requests = $state<SheetRequest[]>([]);
  let subject = $state("MATHS");
  let kind = $state<RequestKind>("revision");
  let focus = $state("");
  // Minutes: a revision to prepare a test is long (worked on paper too), an extra exercise short.
  let minutes = $state<number | null>(30);
  let touched = false;
  $effect(() => {
    if (!touched) minutes = kind === "revision" ? 30 : 10;
  });
  let sent = $state<"" | "plain" | "photos">("");
  let error = $state("");
  let busy = $state("");

  // Photos chosen for the next request, each with a preview.
  let chosen = $state<{ file: File; preview: string }[]>([]);
  let picker: HTMLInputElement | undefined = $state();
  let form: HTMLFormElement | undefined = $state();
  // Photos made smaller but not all sent (connection lost): sent again without choosing them again.
  let unsent = $state<{ id: string; photos: Blob[]; next: number } | null>(null);

  // The text of each photo, as the parent corrects it, by request.
  let drafts = $state<Record<string, string[]>>({});

  const when = (iso: string) =>
    new Date(iso).toLocaleString("fr-FR", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  async function load() {
    try {
      requests = await api.requests();
      for (const request of requests) {
        if (request.status === "to_review" && request.pages && !drafts[request.id]) {
          drafts[request.id] = request.pages.map((page) => page.text);
        }
      }
    } catch {
      error = "Impossible de charger les demandes.";
    }
  }

  function pick(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    const room = MAX_PHOTOS - chosen.length;
    chosen = [...chosen, ...files.slice(0, room).map((file) => ({ file, preview: URL.createObjectURL(file) }))];
    error = files.length > room ? `${MAX_PHOTOS} photos au plus par demande.` : "";
    input.value = ""; // the same photo can be chosen again after removing it
  }

  function drop(index: number) {
    URL.revokeObjectURL(chosen[index].preview);
    chosen = chosen.filter((_, i) => i !== index);
  }

  async function send(id: string, photos: Blob[], from: number) {
    for (let index = from; index < photos.length; index++) {
      busy = `Envoi de la photo ${index + 1} sur ${photos.length}…`;
      try {
        await api.uploadPhoto(id, index + 1, photos[index]);
      } catch {
        unsent = { id, photos, next: index };
        throw new Error("upload");
      }
    }
    unsent = null;
  }

  async function ask(event: Event) {
    event.preventDefault();
    error = "";
    sent = "";
    let photos: Blob[] = [];
    try {
      busy = chosen.length ? "Préparation des photos…" : "Envoi…";
      photos = await Promise.all(chosen.map((photo) => shrink(photo.file)));
    } catch {
      busy = "";
      error = "Une photo n'a pas pu être lue. Reprends-la avec l'appareil photo, ou choisis-la en JPEG.";
      return;
    }
    try {
      const made = await api.createRequest(subject, kind, focus.trim(), minutes, photos.length);
      if (photos.length) await send(made.id, photos, 0);
      sent = photos.length ? "photos" : "plain";
      focus = "";
      chosen.forEach((photo) => URL.revokeObjectURL(photo.preview));
      chosen = [];
    } catch {
      error = unsent
        ? "Toutes les photos ne sont pas parties. Vérifie la connexion, puis « Renvoyer les photos » plus bas."
        : "Impossible d'envoyer la demande. Vérifie la connexion.";
    } finally {
      busy = "";
      await load();
    }
  }

  async function resend() {
    if (!unsent) return;
    error = "";
    try {
      await send(unsent.id, unsent.photos, unsent.next);
      sent = "photos";
    } catch {
      error = "Les photos ne sont toujours pas parties. Vérifie la connexion.";
    } finally {
      busy = "";
      await load();
    }
  }

  async function confirm(request: SheetRequest) {
    error = "";
    try {
      await api.reviewPages(request.id, drafts[request.id]);
      delete drafts[request.id];
      await load();
    } catch {
      error = "Impossible d'envoyer le texte. Vérifie la connexion, et qu'une photo au moins a du texte.";
    }
  }

  async function cancel(request: SheetRequest) {
    if (!window.confirm("Annuler cette demande ? Ses photos seront effacées.")) return;
    error = "";
    try {
      await api.cancelRequest(request.id);
      if (unsent?.id === request.id) unsent = null;
      await load();
    } catch {
      error = "Impossible d'annuler la demande. Vérifie la connexion.";
    }
  }

  /** A request refused for want of a course: the same one again, with photos of the notebook. */
  async function again(request: SheetRequest) {
    subject = request.subject;
    kind = request.kind;
    focus = request.focus;
    if (request.minutes) {
      minutes = request.minutes;
      touched = true;
    }
    sent = "";
    await tick();
    form?.scrollIntoView({ behavior: "smooth" });
    picker?.click();
  }

  onMount(() => {
    void load();
    return () => chosen.forEach((photo) => URL.revokeObjectURL(photo.preview));
  });
</script>

<button class="link" onclick={onexit}>← Retour aux fiches</button>
<h1>Demander une fiche</h1>

<form class="card" onsubmit={ask} bind:this={form}>
  <label for="subject">Matière</label>
  <select id="subject" bind:value={subject}>
    {#each SUBJECTS as [code, name]}<option value={code}>{name}</option>{/each}
  </select>

  <fieldset>
    <legend>Quoi ?</legend>
    <label class="choice"><input type="radio" bind:group={kind} value="revision" /> Une révision sur un point précis</label>
    <label class="choice"><input type="radio" bind:group={kind} value="extra" /> Un exercice en plus</label>
  </fieldset>

  <label for="focus">Sur quoi ? <span class="muted">(facultatif)</span></label>
  <input id="focus" type="text" bind:value={focus} maxlength="300"
    placeholder="par exemple : les fractions égales, le chapitre sur les volcans…" />

  <label for="minutes">Durée</label>
  <select id="minutes" bind:value={minutes} onchange={() => (touched = true)}>
    {#each DURATIONS as [value, name]}<option {value}>{name}</option>{/each}
  </select>
  {#if minutes && minutes >= 30}
    <p class="muted hint">
      Une fiche longue, pour préparer un contrôle : questions de cours, exercices et exercices à rédiger. Elle se
      télécharge en PDF pour être faite au stylo. Il faut à l'ordinateur environ {Math.round(minutes * 0.9)} minutes
      pour l'écrire.
    </p>
  {/if}

  <label for="photos">Photos du cours <span class="muted">(facultatif)</span></label>
  <p class="muted hint">
    Quand le cours est dans le cahier et pas dans École Directe : les encadrés remplis en classe, la trace écrite…
    L'ordinateur lit chaque photo (environ 3 minutes), puis tu relis le texte avant qu'il écrive la fiche.
  </p>
  {#if chosen.length}
    <ul class="photos">
      {#each chosen as photo, index (photo.preview)}
        <li>
          <img src={photo.preview} alt="Photo {index + 1}" />
          <button type="button" class="link" onclick={() => drop(index)}>Retirer</button>
        </li>
      {/each}
    </ul>
  {/if}
  {#if chosen.length < MAX_PHOTOS}
    <input id="photos" class="file" type="file" accept="image/*" multiple bind:this={picker} onchange={pick} />
    <label for="photos" class="button">{chosen.length ? "Ajouter une autre photo" : "Ajouter des photos du cours"}</label>
  {/if}

  <p><button class="primary" disabled={!!busy}>{busy || "Demander"}</button></p>
  {#if sent === "plain"}
    <p class="good" role="status">
      C'est noté. L'ordinateur s'y met dans les 5 minutes s'il est allumé (entre 7 h et 22 h), sinon à la
      soirée, en relisant tout le cours de la matière. Une notification te préviendra dès qu'elle est prête.
    </p>
  {:else if sent === "photos"}
    <p class="good" role="status">
      Photos envoyées. L'ordinateur les lit dès qu'il est allumé (entre 7 h et 22 h), environ 3 minutes par photo.
      Une notification te demandera ensuite de relire le texte, puis la fiche sera écrite.
    </p>
  {/if}
  {#if error}<p class="error">{error}</p>{/if}
</form>

{#if requests.length}
  <h2>Demandes récentes</h2>
  {#each requests as request (request.id)}
    <div class="card">
      <div class="muted">
        {subjectName(request.subject)} · {request.kind === "revision" ? "révision" : "exercice en plus"}{request.minutes
          ? ` · ${request.minutes} min`
          : ""}{request.photos ? ` · ${request.photos} photo${request.photos > 1 ? "s" : ""}` : ""} · {when(request.created_at)}
      </div>
      {#if request.focus}<p style="margin:0.3rem 0">« {request.focus} »</p>{/if}
      {#if request.status === "photos"}
        <p class="muted">Toutes les photos ne sont pas arrivées.</p>
        <div class="row actions">
          {#if unsent?.id === request.id}<button class="primary" onclick={resend}>Renvoyer les photos</button>{/if}
          <button class="link" onclick={() => cancel(request)}>Annuler</button>
        </div>
      {:else if request.status === "reading"}
        <p class="muted">L'ordinateur lit les photos du cahier dès qu'il est allumé (environ 3 minutes par photo).</p>
        <button class="link" onclick={() => cancel(request)}>Annuler</button>
      {:else if request.status === "to_review" && request.pages && drafts[request.id]}
        <p><strong>À relire :</strong> voici ce que l'ordinateur a lu. Corrige ce qui ne va pas en regardant le
          cahier : la fiche sera écrite à partir de ce texte.</p>
        {#each request.pages as page, index}
          <label for="page-{request.id}-{index}">Photo {index + 1}</label>
          {#if page.problem}<p class="bad hint">{page.problem}{page.text ? "" : " : tape le texte toi-même, ou laisse vide."}</p>{/if}
          <textarea id="page-{request.id}-{index}" rows="10" maxlength="6000" bind:value={drafts[request.id][index]}></textarea>
        {/each}
        <div class="row actions">
          <button class="primary" disabled={!drafts[request.id].some((text) => text.trim())} onclick={() => confirm(request)}>
            Faire la fiche avec ce texte
          </button>
          <button class="link" onclick={() => cancel(request)}>Annuler la demande</button>
        </div>
      {:else if request.status === "pending"}
        <p class="muted">En préparation : l'ordinateur s'en occupe dès qu'il est allumé.</p>
      {:else if request.status === "done" && request.sheet_id}
        <div class="row spread">
          <span class="good">Prête</span>
          <button class="primary" onclick={() => onopen(request.sheet_id!)}>Ouvrir</button>
        </div>
      {:else}
        <p class="bad">Pas possible : {request.reason}</p>
        {#if request.reason !== "Annulée."}
          <button class="link" onclick={() => again(request)}>Redemander avec des photos du cahier</button>
        {/if}
      {/if}
    </div>
  {/each}
{/if}

<style>
  label { display: block; margin: 0.8rem 0 0.3rem; font-weight: 600; }
  select, input[type="text"], textarea { width: 100%; }
  textarea { font: inherit; padding: 0.5rem; box-sizing: border-box; }
  fieldset { border: none; padding: 0; margin: 0.8rem 0 0; }
  legend { font-weight: 600; margin-bottom: 0.3rem; }
  .hint { font-size: 0.88rem; margin: 0.4rem 0 0; }
  .choice { font-weight: 400; display: flex; gap: 0.5rem; align-items: center; min-height: 2.6rem; margin: 0; }
  /* The real input is hidden: its label is the button (the input's own look cannot be styled). */
  .file { position: absolute; width: 1px; height: 1px; opacity: 0; }
  label.button { display: inline-block; font-weight: 400; cursor: pointer; }
  .photos { list-style: none; padding: 0; margin: 0.5rem 0; display: flex; flex-wrap: wrap; gap: 0.6rem; }
  .photos li { display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }
  .photos img { width: 5.5rem; height: 7rem; object-fit: cover; border-radius: 6px; border: 1px solid var(--line); }
  .actions { gap: 1.2rem; margin-top: 0.6rem; flex-wrap: wrap; }
</style>
