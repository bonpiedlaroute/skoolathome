<script lang="ts">
  import { api } from "../lib/api";
  import type { Device } from "../lib/types";

  let { onpaired }: { onpaired: (device: Device) => void } = $props();

  let code = $state("");
  let busy = $state(false);
  let error = $state("");

  async function submit(event: Event) {
    event.preventDefault();
    busy = true;
    error = "";
    try {
      onpaired(await api.pairWithCode(code.trim(), "Parent"));
    } catch {
      error = "Code refusé. Si un appareil parent existe déjà, utilise son QR code.";
    } finally {
      busy = false;
    }
  }
</script>

<h1>Révisions</h1>
<div class="card">
  <h2 style="margin-top:0">Cet appareil n'est pas encore associé</h2>
  <p>
    <strong>Tablette ou téléphone de la famille :</strong> sur l'appareil d'un parent, ouvre « Appareils »,
    crée un QR code, puis scanne-le avec l'appareil photo de celui-ci.
  </p>
</div>
<form class="card" onsubmit={submit}>
  <h2 style="margin-top:0">Premier appareil parent</h2>
  <p class="muted">Saisis le code d'amorçage défini dans les réglages du serveur. Il ne sert qu'une fois.</p>
  <input type="password" bind:value={code} autocomplete="off" aria-label="Code d'amorçage" />
  {#if error}<p class="error">{error}</p>{/if}
  <p><button class="primary" disabled={busy || code.trim().length === 0}>Associer cet appareil</button></p>
</form>
