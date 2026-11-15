<script lang="ts">
  import { onMount } from "svelte";

  import Devices from "./components/Devices.svelte";
  import Home from "./components/Home.svelte";
  import Pair from "./components/Pair.svelte";
  import PrintSheet from "./components/PrintSheet.svelte";
  import Requests from "./components/Requests.svelte";
  import SheetRunner from "./components/SheetRunner.svelte";
  import { api, ApiError } from "./lib/api";
  import type { Device } from "./lib/types";

  let device = $state<Device | null>(null);
  let loading = $state(true);
  let error = $state("");
  let route = $state(location.hash.slice(1) || "/");

  // #/sheet/<id>, #/sheet/<id>/together, #/sheet/<id>/review, #/sheet/<id>/print
  const sheetRoute = $derived.by(() => {
    const match = /^\/sheet\/([^/]+)(?:\/(together|review|print))?$/.exec(route);
    return match ? { id: decodeURIComponent(match[1]), view: match[2] ?? "choose" } : null;
  });

  const go = (path: string) => (location.hash = path);
  const sheetPath = (id: string, view = "") => `/sheet/${encodeURIComponent(id)}${view ? `/${view}` : ""}`;

  // Who this device is, remembered so the app also starts without network.
  // Not a secret: the session itself is an HttpOnly cookie.
  const KNOWN = "skoolathome-device";

  function remember(known: Device | null) {
    try {
      if (known) localStorage.setItem(KNOWN, JSON.stringify(known));
      else localStorage.removeItem(KNOWN);
    } catch {
      // storage blocked: the app only needs the network to start
    }
  }

  function remembered(): Device | null {
    try {
      return JSON.parse(localStorage.getItem(KNOWN) ?? "null") as Device | null;
    } catch {
      return null;
    }
  }

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
      remember(device);
    } catch (problem) {
      if (problem instanceof ApiError && problem.status === 401) {
        remember(null); // revoked, or never paired
      } else if (!token && remembered()) {
        device = remembered(); // no network: work with the sheets already loaded
      } else {
        error = token ? "Ce QR code n'est plus valable : demande-en un nouveau." : "Impossible de joindre le serveur. Vérifie la connexion.";
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
  <Pair onpaired={(paired) => ((device = paired), (error = ""), remember(paired))} />
{:else if sheetRoute?.view === "print"}
  <PrintSheet id={sheetRoute.id} onexit={() => go(sheetPath(sheetRoute.id))} />
{:else if sheetRoute}
  {#key route}
    <SheetRunner
      id={sheetRoute.id}
      start={sheetRoute.view as "choose" | "together" | "review"}
      onexit={() => go("/")}
      onprint={() => go(sheetPath(sheetRoute.id, "print"))}
    />
  {/key}
{:else if route === "/devices" && device.role === "parent"}
  <Devices onexit={() => go("/")} />
{:else if route === "/requests" && device.role === "parent"}
  <Requests onexit={() => go("/")} onopen={(id) => go(sheetPath(id))} />
{:else}
  <Home {device} onopen={(id, how) => go(sheetPath(id, how ?? ""))} ondevices={() => go("/devices")}
    onrequests={() => go("/requests")} />
{/if}
