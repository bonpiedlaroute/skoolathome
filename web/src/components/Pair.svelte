<script lang="ts">
  // A device not paired yet: with a code made on a device already paired, or, for the very
  // first parent device, with the bootstrap code chosen when the site was installed.
  import { api, ApiError } from "../lib/api";
  import type { Device } from "../lib/types";

  let { onpaired }: { onpaired: (device: Device) => void } = $props();

  let code = $state("");
  let bootstrap = $state("");
  let busy = $state(false);
  let codeError = $state("");
  let bootstrapError = $state("");

  async function pairWithCode(event: Event) {
    event.preventDefault();
    busy = true;
    codeError = "";
    try {
      onpaired(await api.pairWithToken(code.trim()));
    } catch {
      codeError = "Code refusé : il a peut-être expiré (10 minutes) ou déjà servi. Crée-en un nouveau.";
    } finally {
      busy = false;
    }
  }

  async function pairFirst(event: Event) {
    event.preventDefault();
    busy = true;
    bootstrapError = "";
    try {
      onpaired(await api.pairWithCode(bootstrap.trim(), "Parent"));
    } catch (problem) {
      bootstrapError =
        problem instanceof ApiError && problem.message.includes("already paired")
          ? "Un appareil parent existe déjà : associe celui-ci avec un code créé depuis « Appareils » sur cet appareil."
          : "Code d'amorçage refusé.";
    } finally {
      busy = false;
    }
  }
</script>

<h1>Révisions</h1>
<p>Cet appareil n'est pas encore associé. C'est à faire une seule fois par appareil.</p>

<form class="card" onsubmit={pairWithCode}>
  <h2 style="margin-top:0">J'ai un code d'association</h2>
  <ol>
    <li>Sur un appareil déjà associé comme parent (téléphone, tablette ou ordinateur), ouvre <strong>Appareils</strong>.</li>
    <li>Dans « Associer un nouvel appareil », choisis <strong>Parent</strong> ou <strong>Enfant</strong>, puis <strong>Créer le code</strong>.</li>
    <li>Tape ici le code affiché. Il est valable 10 minutes.</li>
  </ol>
  <input type="text" bind:value={code} placeholder="par exemple K7PM-4QXN" autocomplete="off" autocapitalize="characters"
    spellcheck="false" maxlength="20" aria-label="Code d'association" class="code" />
  {#if codeError}<p class="error">{codeError}</p>{/if}
  <p><button class="primary" disabled={busy || code.trim().length < 8}>Associer cet appareil</button></p>
  <p class="muted">Sur une tablette ou un téléphone, tu peux aussi scanner le QR code affiché avec le code.</p>
</form>

<details class="card">
  <summary><strong>C'est le tout premier appareil parent</strong></summary>
  <form onsubmit={pairFirst}>
    <p>
      Le premier appareil s'associe avec le <strong>code d'amorçage</strong>, choisi par la personne qui a installé le site
      (la variable <code>SKOOLATHOME_BOOTSTRAP_CODE</code> des réglages Vercel). Il ne sert qu'une fois : ensuite, chaque
      appareil s'associe avec un code créé depuis « Appareils ».
    </p>
    <input type="password" bind:value={bootstrap} autocomplete="off" aria-label="Code d'amorçage" />
    {#if bootstrapError}<p class="error">{bootstrapError}</p>{/if}
    <p><button class="primary" disabled={busy || bootstrap.trim().length === 0}>Associer comme premier parent</button></p>
  </form>
</details>

<style>
  ol { padding-left: 1.3rem; }
  li { margin: 0.35rem 0; }
  .code { width: 100%; font-size: 1.4rem; letter-spacing: 0.15em; text-transform: uppercase; text-align: center; }
  summary { cursor: pointer; min-height: 2.6rem; display: flex; align-items: center; }
</style>
