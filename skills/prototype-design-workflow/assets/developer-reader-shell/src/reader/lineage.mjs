import {sha256Hex} from './sha256.mjs';
export const CORE_READER_ITEMS = Object.freeze(['change-overview', 'collapse-expand-all', 'return-previous-prototype', 'full-development-snapshot', 'change-kind-color', 'current-location', 'bidirectional-visible-connector', 'fact-source-details']);
const stable = (value) => JSON.stringify(value);
const setEq = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

export function validateLineage({manifest, previousManifest, previousManifestSha256, previousSnapshotSha256, baselineReceipt}) {
  const errors = [];
  const lineage = manifest?.delivery?.lineage;
  const current = manifest?.cards ?? [];
  if (lineage?.mode === 'initial') {
    if (lineage.previousManifestSha256 != null || lineage.previousSnapshotSha256 != null || lineage.baselineReceiptSha256 != null) errors.push('E_LINEAGE_INPUT_REQUIRED initial must not bind previous inputs');
    if (!setEq(current.map(({cardId}) => cardId), lineage.addedCardIds ?? [])) errors.push('E_LINEAGE_CARD_SET initial cards must all be added');
  } else if (lineage?.mode === 'revision') {
    if (!previousManifest || !previousManifestSha256 || !previousSnapshotSha256 || !baselineReceipt) return ['E_LINEAGE_INPUT_REQUIRED'];
    if (previousManifestSha256 !== lineage.previousManifestSha256 || previousSnapshotSha256 !== lineage.previousSnapshotSha256) errors.push('E_LINEAGE_HASH_MISMATCH');
    if (baselineReceipt.previousManifest?.sha256 !== lineage.previousManifestSha256 || baselineReceipt.previousSnapshot?.sha256 !== lineage.previousSnapshotSha256) errors.push('E_LINEAGE_HASH_MISMATCH receipt binding');
    const previous = previousManifest.cards ?? [];
    const previousIds = previous.map(({cardId}) => cardId), currentIds = current.map(({cardId}) => cardId);
    const classes = ['preservedCardIds', 'modifiedCardIds', 'addedCardIds', 'removedCardIds'].flatMap((key) => lineage[key] ?? []);
    if (!setEq(classes, [...previousIds, ...currentIds].filter((id, index, all) => all.indexOf(id) === index)) || new Set(classes).size !== classes.length) errors.push('E_LINEAGE_CARD_SET');
    const byCurrent = new Map(current.map((card) => [card.cardId, card]));
    const byPrevious = new Map(previous.map((card) => [card.cardId, card]));
    for (const id of lineage.addedCardIds ?? []) if (byPrevious.has(id) || !byCurrent.has(id)) errors.push(`E_LINEAGE_CARD_SET added ${id}`);
    for (const id of [...(lineage.preservedCardIds ?? []), ...(lineage.modifiedCardIds ?? [])]) if (!byPrevious.has(id) || !byCurrent.has(id)) errors.push(`E_LINEAGE_CARD_SET retained ${id}`);
    for (const id of lineage.removedCardIds ?? []) if (!byPrevious.has(id) || byCurrent.has(id)) errors.push(`E_LINEAGE_CARD_SET removed ${id}`);
    const normalize = (card) => { const copy = structuredClone(card); delete copy.displayNumber; return stable(copy); };
    for (const id of lineage.preservedCardIds ?? []) if (!byCurrent.has(id) || !byPrevious.has(id) || normalize(byCurrent.get(id)) !== normalize(byPrevious.get(id))) errors.push(`E_PRESERVED_CARD_DRIFT ${id}`);
    for (const id of lineage.modifiedCardIds ?? []) {
      const card = byCurrent.get(id), h = card?.history?.at(-1);
      const prior = byPrevious.get(id);
      const priorCardHash = prior ? sha256Hex(stable(prior)) : null;
      if (!card || !prior || !/^[a-f0-9]{64}$/.test(h?.previousCardSha256 ?? '') || h.previousCardSha256 !== priorCardHash) errors.push(`E_LINEAGE_CARD_HASH ${id}`);
      if (!card || !prior || !h?.reason?.trim() || !Array.isArray(h?.sourceRefs) || !h.sourceRefs.length) errors.push(`E_LINEAGE_CARD_SET modified ${id}`);
    }
    const decisions = new Map((lineage.removalDecisions ?? []).map((item) => [item.cardId, item]));
    for (const id of lineage.removedCardIds ?? []) if (byCurrent.has(id) || !decisions.get(id)?.reason?.trim() || !decisions.get(id)?.sourceRefs?.length) errors.push(`E_LINEAGE_CARD_SET removed ${id}`);
    const oldItems = previousManifest.delivery?.readerComposition?.customItems ?? [];
    const currentItems = manifest.delivery.readerComposition?.customItems ?? [];
    for (const item of oldItems) if (!currentItems.some((candidate) => candidate.itemId === item.itemId) && !(manifest.delivery.readerComposition?.removedItems ?? []).some((removed) => removed.itemId === item.itemId && removed.sourceRefs?.length)) errors.push(`E_READER_COMPOSITION_REGRESSION ${item.itemId}`);
  } else errors.push('E_LINEAGE_CARD_SET delivery.lineage.mode');
  return errors;
}
