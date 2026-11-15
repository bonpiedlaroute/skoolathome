<script lang="ts">
  // "Signaler": one tap on a reason, an optional comment. Sent through the outbox.
  import { api } from "../lib/api";
  import type { ReportReason } from "../lib/types";

  let {
    sheetId,
    itemId,
    traceId,
    onclose,
  }: { sheetId: string; itemId: string | null; traceId?: string | null; onclose: (sent: boolean) => void } = $props();

  const REASONS: [ReportReason, string][] = [
    ["wrong_answer", "La réponse attendue est fausse"],
    ["not_covered", "On ne l'a pas vu en cours"],
    ["unclear", "La question n'est pas claire"],
    ["too_hard", "Trop difficile"],
    ["too_easy", "Trop facile"],
  ];

  let reason = $state<ReportReason | null>(null);
  let comment = $state("");

  function send() {
    if (!reason) return;
    api.report(sheetId, itemId, reason, comment.trim(), traceId);
    onclose(true);
  }
</script>

<div class="backdrop no-print" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose(false)}>
  <div class="dialog card" role="dialog" aria-modal="true" aria-labelledby="report-title">
    <h2 id="report-title" style="margin-top:0">Signaler cette question</h2>
    <div class="reasons">
      {#each REASONS as [code, label]}
        <button type="button" class:selected={reason === code} onclick={() => (reason = code)}>{label}</button>
      {/each}
    </div>
    <textarea bind:value={comment} maxlength="500" placeholder="Un détail ? (facultatif)" aria-label="Commentaire"></textarea>
    <div class="row" style="margin-top:0.8rem">
      <button class="primary" disabled={!reason} onclick={send}>Envoyer</button>
      <button onclick={() => onclose(false)}>Annuler</button>
    </div>
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.45); display: grid; place-items: end center; z-index: 10; }
  .dialog { width: min(40rem, 100%); margin: 0; border-radius: 18px 18px 0 0; padding-bottom: calc(1rem + env(safe-area-inset-bottom)); }
  .reasons { display: grid; gap: 0.5rem; margin-bottom: 0.8rem; }
  .reasons button { text-align: left; }
  .reasons button.selected { border: 2px solid var(--accent); }
</style>
