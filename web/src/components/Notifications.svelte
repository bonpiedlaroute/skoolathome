<script lang="ts">
  // Turn on notifications for this device. On an iPad or iPhone they need the app
  // added to the home screen first (iPadOS 16.4 or later).
  import { onMount } from "svelte";

  import { api } from "../lib/api";

  type Status = "checking" | "unsupported" | "install-first" | "off" | "on" | "denied" | "not-configured";
  let status = $state<Status>("checking");
  let message = $state("");

  const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const standalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
  const apple = () => /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function keyBytes(base64url: string): ArrayBuffer {
    const padded = (base64url + "===".slice((base64url.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0)).buffer as ArrayBuffer;
  }

  async function register(): Promise<void> {
    const { public_key } = await api.pushKey();
    if (!public_key) {
      status = "not-configured";
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    const subscription =
      (await registration.pushManager.getSubscription()) ??
      (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) }));
    await api.pushSubscribe(subscription.toJSON());
    status = "on";
  }

  async function enable() {
    message = "";
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        status = "denied";
        return;
      }
      await register();
    } catch {
      message = "L'activation a échoué. Réessaie plus tard.";
    }
  }

  async function test() {
    message = "";
    try {
      const { sent } = await api.pushTest();
      message = sent ? "Notification envoyée : elle doit arriver dans quelques secondes." : "Aucune notification envoyée.";
    } catch {
      message = "Le test a échoué.";
    }
  }

  onMount(async () => {
    if (!supported()) status = apple() && !standalone() ? "install-first" : "unsupported";
    else if (Notification.permission === "denied") status = "denied";
    else if (Notification.permission === "granted") await register().catch(() => (status = "off"));
    else status = "off";
  });
</script>

{#if status === "off"}
  <div class="card row spread no-print">
    <span>Être prévenu quand une fiche arrive</span>
    <button onclick={enable}>Activer les notifications</button>
  </div>
{:else if status === "install-first"}
  <div class="card no-print">
    <p style="margin:0">
      Pour recevoir les notifications, ajoute l'app à l'écran d'accueil : bouton Partager, puis « Sur l'écran
      d'accueil ». Ouvre-la ensuite depuis son icône.
    </p>
  </div>
{:else if status === "on"}
  <p class="muted no-print">Notifications activées · <button class="link" onclick={test}>Tester</button></p>
{:else if status === "denied"}
  <p class="muted no-print">Notifications refusées sur cet appareil. Pour changer d'avis : réglages du navigateur.</p>
{:else if status === "not-configured"}
  <p class="muted no-print">Les notifications ne sont pas encore configurées sur le serveur.</p>
{/if}
{#if message}<p class="muted no-print">{message}</p>{/if}
