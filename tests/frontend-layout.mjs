// Run with node tests/frontend-layout.mjs. Exercises the shipped module.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL(
  "../custom_components/ecoflow_modbus/frontend/compact-controls.js", import.meta.url
), "utf8");
class Root {
  constructor(host) { this.host = host; this.styles = new Map(); this.children = []; }
  getElementById(id) { return this.styles.get(id); }
  append(style) {
    this.styles.set(style.id, style);
    style.remove = () => this.styles.delete(style.id);
  }
  querySelector() { return this.generic; }
  querySelectorAll() { return this.children; }
}
class Row {
  updated(...args) { this.calls.push(args); return "original-result"; }
  connectedCallback() { this.connected = true; }
}
class Card { updated() { return "card-result"; } }
const dom = new Root();
const listeners = new Map();
const win = {
  addEventListener(name, handler) { listeners.set(name, handler); },
  requestAnimationFrame(callback) { queueMicrotask(callback); },
};
let define;
const defined = new Promise((resolve) => { define = resolve; });
const context = vm.createContext({
  customElements: { whenDefined: () => defined, get: (name) => name === "hui-number-entity-row" ? Row : Card },
  document: Object.assign(dom, { createElement: () => ({}) }), window: win, console,
});
const existing = row();
dom.children = [existing.item];
vm.runInContext(source, context);
const original = Row.prototype.updated;
assert.equal(Row.prototype.updated, original); // Element has not loaded yet.
define();
await defined;
await Promise.resolve();
assert.notEqual(Row.prototype.updated, original);
await new Promise(setImmediate);
assert.equal(existing.item.shadowRoot.styles.size, 1, "Style existing unchanged rows when module arrives late");
const wrapped = Row.prototype.updated;
vm.runInContext(source, context);
await Promise.resolve();
assert.equal(Row.prototype.updated, wrapped); // One wrapper even on duplicate load.

function row(platform = "ecoflow_modbus", inDevicePage = true) {
  const item = new Row();
  item.calls = [];
  item.localName = "hui-number-entity-row";
  item.isConnected = true;
  item.updateComplete = Promise.resolve();
  item.shadowRoot = new Root(item);
  const generic = { config: { entity: "number.renamed_custom_id" },
                    updateComplete: Promise.resolve() };
  generic.shadowRoot = new Root(generic);
  item.shadowRoot.generic = generic;
  const card = { localName: inDevicePage ? "ha-device-entities-card" : "hui-entities-card",
                 entities: [{ entity_id: generic.config.entity, platform }] };
  item.parentNode = { parentNode: new Root(card) };
  return { item, generic, card };
}
async function update(item) {
  assert.equal(item.updated("changed"), "original-result");
  await new Promise(setImmediate);
}
const eco = row();
await update(eco.item);
assert.equal(eco.item.shadowRoot.styles.size, 1);
assert.equal(eco.generic.shadowRoot.styles.size, 1);
assert.match([...eco.item.shadowRoot.styles.values()][0].textContent, /120px/);
assert.match([...eco.generic.shadowRoot.styles.values()][0].textContent, /white-space: normal/);
assert.deepEqual(eco.item.calls, [["changed"]]);
await update(eco.item);
assert.equal(eco.item.shadowRoot.styles.size, 1); // No per-update accumulation.

for (const sample of [row("other_integration"), row("ecoflow_modbus", false), row(undefined, false)]) {
  await update(sample.item);
  assert.equal(sample.item.shadowRoot.styles.size, 0);
  assert.equal(sample.generic.shadowRoot.styles.size, 0);
}
eco.card.entities[0].platform = "other_integration";
await update(eco.item);
assert.equal(eco.item.shadowRoot.styles.size, 0); // Reused row loses EcoFlow styles.
assert.equal(eco.generic.shadowRoot.styles.size, 0);
const detached = row();
detached.item.isConnected = false;
await update(detached.item);
assert.equal(detached.item.shadowRoot.styles.size, 0);
const reconnect = row();
dom.children = [reconnect.item];
listeners.get("location-changed")();
await new Promise(setImmediate);
assert.equal(reconnect.item.shadowRoot.styles.size, 1, "Navigation styles unchanged remounted rows");
const rendered = row();
const renderedCard = Object.assign(new Card(), rendered.card, {
  isConnected: true, updateComplete: Promise.resolve(), shadowRoot: new Root(),
});
renderedCard.shadowRoot.children = [rendered.item];
assert.equal(renderedCard.updated(), "card-result");
await new Promise(setImmediate);
assert.equal(rendered.item.shadowRoot.styles.size, 1, "Card renders style child rows");
const compactRegistry = row();
delete compactRegistry.card.entities[0].platform;
compactRegistry.card.hass = { entities: { "number.renamed_custom_id": { platform: "ecoflow_modbus" } } };
await update(compactRegistry.item);
assert.equal(compactRegistry.item.shadowRoot.styles.size, 1);
const fallback = row();
delete fallback.card.entities[0].platform;
fallback.card.parentNode = new Root({ localName: "ha-config-device-page", deviceId: "eco", hass: {
  devices: { eco: { identifiers: [["ecoflow_modbus", "device-id"]] } }
} });
await update(fallback.item);
assert.equal(fallback.item.shadowRoot.styles.size, 1);
fallback.card.entities[0].platform = "other_integration";
await update(fallback.item);
assert.equal(fallback.item.shadowRoot.styles.size, 0, "Explicit foreign platform overrides device fallback");
console.log("Passed frontend checks: existing unchanged rows, lazy definitions, native updates, device navigation, card rerenders, compact registries, scoped styles and cleanup.");
