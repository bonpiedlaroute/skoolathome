import "katex/dist/katex.min.css";
import { mount } from "svelte";

import App from "./App.svelte";
import "./app.css";
import { warmCache } from "./lib/api";
import { startOutbox } from "./lib/outbox.svelte";

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  // The first time, the page loaded before the service worker took over: load the data
  // again once it does, so that it is cached for the next start without network.
  navigator.serviceWorker.addEventListener("controllerchange", () => void warmCache());
  navigator.serviceWorker.register("/sw.js").catch(() => {
    // no offline mode, the app still works
  });
}
startOutbox();

mount(App, { target: document.getElementById("app")! });
