<script lang="ts">
  // Parent view: paired devices, revocation, QR code to pair a new one.
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import type { Device } from "../lib/types";

  let { onexit }: { onexit: () => void } = $props();

  let devices = $state<Device[]>([]);
  let role = $state<"child" | "parent">("child");
  let label = $state("iPad");
  let qr = $state<{ qr_svg: string; expires_in_minutes: number } | null>(null);
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
  <div class="row">
    <select bind:value={role} style="flex:1" aria-label="Rôle">
      <option value="child">Enfant</option>
      <option value="parent">Parent</option>
    </select>
    <input type="text" bind:value={label} style="flex:2" maxlength="60" aria-label="Nom de l'appareil" />
  </div>
  <p><button class="primary" disabled={label.trim() === ""}>Créer le QR code</button></p>
  {#if qr}
    <div class="qr">{@html qr.qr_svg}</div>
    <p class="muted">
      Scanne-le avec l'appareil photo de l'appareil à associer. Valable {qr.expires_in_minutes} minutes, une seule fois.
    </p>
  {/if}
</form>

<style>
  .qr { background: #fff; padding: 0.5rem; border-radius: 12px; display: inline-block; }
  .qr :global(svg) { display: block; width: min(16rem, 70vw); height: auto; }
</style>
