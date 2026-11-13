<script lang="ts">
  import { onMount } from "svelte";

  import Devices from "./components/Devices.svelte";
  import Home from "./components/Home.svelte";
  import Pair from "./components/Pair.svelte";
  import SheetRunner from "./components/SheetRunner.svelte";
  import { api, ApiError } from "./lib/api";
  import type { Device } from "./lib/types";

  let device = $state<Device | null>(null);
  let loading = $state(true);
  let error = $state("");
  let route = $state(location.hash.slice(1) || "/");

  const go = (path: string) => (location.hash = path);

  async function start() {
    const token = route.startsWith("/pair/") ? route.slice("/pair/".length) : "";
    try {
      if (token) {
        // Arriving from a QR code: pair this device, then forget the token.
        history.replaceState(null, "", location.pathname);
        route = "/";
        device = await api.pairWithToken(token);
      } else {
        device = await api.me();
      }
    } catch (problem) {
      if (!(problem instanceof ApiError) || problem.status !== 401) {
        error = token
          ? "Ce QR code n'est plus valable : demande-en un nouveau."
          : "Impossible de joindre le serveur. Vérifie la connexion.";
      }
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    const onHash = () => (route = location.hash.slice(1) || "/");
    window.addEventListener("hashchange", onHash);
    start();
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

{#if loading}
  <p class="muted">Chargement…</p>
{:else if !device}
  {#if error}<p class="error">{error}</p>{/if}
  <Pair onpaired={(paired) => ((device = paired), (error = ""))} />
{:else if route.startsWith("/sheet/")}
  <SheetRunner id={decodeURIComponent(route.slice("/sheet/".length))} onexit={() => go("/")} />
{:else if route === "/devices" && device.role === "parent"}
  <Devices onexit={() => go("/")} />
{:else}
  <Home {device} onopen={(id) => go(`/sheet/${encodeURIComponent(id)}`)} ondevices={() => go("/devices")} />
{/if}
