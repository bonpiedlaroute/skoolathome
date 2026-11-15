<script lang="ts">
  // Parent view: paired devices, revocation, QR code to pair a new one.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import type { Device } from "../lib/types";

  let { onexit }: { onexit: () => void } = $props();

  let devices = $state<Device[]>([]);
  let role = $state<"child" | "parent">("child");
  let label = $state("iPad");
  let qr = $state<{ code: string; url: string; qr_svg: string; expires_in_minutes: number } | null>(null);
  let copied = $state(false);
  const canShare = typeof navigator.share === "function";

  /** On a phone, the share sheet (Messages, WhatsApp…); elsewhere, the clipboard. */
  async function sendLink() {
    if (!qr) return;
    try {
      if (canShare) {
        await navigator.share({
          title: "Révisions",
          text: `Pour associer ton appareil aux révisions, touche ce lien (valable ${qr.expires_in_minutes} min) :`,
          url: qr.url,
        });
      } else {
        await navigator.clipboard.writeText(qr.url);
        copied = true;
      }
    } catch {
      // share sheet closed, or clipboard refused: the code stays on screen
    }
  }
  let error = $state("");

  const when = (iso?: string | null) =>
    iso ? new Date(iso).toLocaleString("fr-FR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "jamais";

  async function load() {
    try {
      devices = await api.devices();
    } catch {
      error = "Impossible de charger les appareils.";
    }
  }

  async function createQr(event: Event) {
    event.preventDefault();
    error = "";
    try {
      qr = await api.pairingToken(role, label.trim());
    } catch {
      error = "Impossible de créer le QR code.";
    }
  }

  async function revoke(device: Device) {
    if (!confirm(`Retirer l'accès de « ${device.label} » ?`)) return;
    try {
      await api.revoke(device.id);
      await load();
    } catch {
      error = "Impossible de retirer cet appareil.";
    }
  }

  onMount(load);
</script>

<button class="link" onclick={onexit}>← Retour aux fiches</button>
<h1>Appareils</h1>
{#if error}<p class="error">{error}</p>{/if}

{#each devices as device (device.id)}
  <div class="card row spread">
    <div>
      <strong>{device.label}</strong>
      <span class="muted">· {device.role === "parent" ? "parent" : "enfant"}{device.is_this_device ? " · cet appareil" : ""}</span>
      <div class="muted">Dernière visite : {when(device.last_seen_at)}</div>
    </div>
    {#if !device.is_this_device}<button onclick={() => revoke(device)}>Retirer</button>{/if}
  </div>
{/each}

<form class="card" onsubmit={createQr}>
  <h2 style="margin-top:0">Associer un nouvel appareil</h2>
  <p class="muted">Pour un autre parent, ton ordinateur, ou la tablette de l'enfant.</p>
  <div class="row">
    <select bind:value={role} style="flex:1" aria-label="Rôle">
      <option value="child">Enfant</option>
      <option value="parent">Parent</option>
    </select>
    <input type="text" bind:value={label} style="flex:2" maxlength="60" aria-label="Nom de l'appareil" />
  </div>
  <p><button class="primary" disabled={label.trim() === ""}>Créer le code</button></p>
  {#if qr}
    <p>Sur l'appareil à associer, ouvre <strong>{location.host}</strong> et tape ce code :</p>
    <p class="code">{qr.code}</p>
    <p class="muted">Valable {qr.expires_in_minutes} minutes, une seule fois.</p>
    <p class="muted">Sur une tablette ou un téléphone, tu peux aussi scanner ce QR code avec l'appareil photo :</p>
    <div class="qr">{@html qr.qr_svg}</div>
    <p>
      <button type="button" class="primary" onclick={sendLink}>
        {canShare ? "Envoyer le lien par message" : copied ? "Lien copié" : "Copier le lien, pour l'envoyer par message"}
      </button>
    </p>
  {/if}
</form>

<style>
  .qr { background: #fff; padding: 0.5rem; border-radius: 12px; display: inline-block; }
  .qr :global(svg) { display: block; width: min(16rem, 70vw); height: auto; }
  .code { font-size: 2rem; font-weight: 700; letter-spacing: 0.15em; font-family: monospace; margin: 0.4rem 0; }
</style>
