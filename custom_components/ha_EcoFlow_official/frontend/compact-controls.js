/* Compact native number controls on EcoFlow device pages only.
 * Keep native service handling; process existing rows as well as later renders.
 */
(() => {
  const DOMAIN = "ecoflow_modbus";
  const STYLE_ID = "ecoflow-compact-number";
  const PATCHED = Symbol.for("ecoflow_modbus.compactNumbers.v2");
  if (window[PATCHED]) return;
  window[PATCHED] = true;
  const INPUT_CSS = `
    .box, .flex:has(ha-textfield), .flex:has(ha-outlined-text-field) {
      flex: 0 0 120px !important;
      width: 120px !important;
      min-width: 0 !important;
      max-width: 120px !important;
    }
    ha-input, ha-textfield, ha-outlined-text-field {
      width: 120px !important;
      min-width: 0 !important;
      max-width: 120px !important;
      box-sizing: border-box;
    }
  `;
  const NAME_CSS = `
    .row { min-height: 56px; height: auto; }
    .info, .info > * {
      min-width: 0;
      white-space: normal !important;
      overflow-wrap: anywhere;
      text-overflow: clip !important;
      overflow: visible !important;
    }
    .info { flex: 1 1 0 !important; padding-block: 8px; }
  `;

  function deviceCard(element) {
    for (let node = element; node; node = node.parentNode || node.host) {
      if (node.localName === "ha-device-entities-card") return node;
    }
    return undefined;
  }

  function setStyle(root, css, enabled) {
    if (!root) return;
    let style = root.getElementById(STYLE_ID);
    if (!enabled) {
      style?.remove();
      return;
    }
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      style.textContent = css;
      root.append(style);
    }
    style.textContent = css;
  }

  async function updateLayout(row) {
    await row.updateComplete;
    if (!row.isConnected) return;
    const generic = row.shadowRoot?.querySelector("hui-generic-entity-row");
    if (!generic) return;
    await generic.updateComplete;
    if (!row.isConnected) return;
    const card = deviceCard(row);
    const entityId = generic.config?.entity;
    const entry = card?.entities?.find((entity) => entity.entity_id === entityId);
    const hass = card?.hass || row.hass;
    const platform = entry?.platform || hass?.entities?.[entityId]?.platform;
    // Older frontend registry payloads may omit platform. A device identifier
    // is a fallback only when no other integration is explicitly identified.
    let page = card;
    while (page && page.localName !== "ha-config-device-page") {
      page = page.parentNode || page.host;
    }
    const device = page?.hass?.devices?.[page.deviceId];
    const ownedDevice = device?.identifiers?.some(([domain]) => domain === DOMAIN);
    const enabled = Boolean(card && entry && (
      platform === DOMAIN || (!platform && ownedDevice)
    ));
    setStyle(row.shadowRoot, INPUT_CSS, enabled);
    setStyle(generic.shadowRoot, NAME_CSS, enabled);
  }

  function scheduleRow(row) {
    updateLayout(row).catch((error) => {
      console.debug("EcoFlow compact control layout unavailable", error);
    });
  }

  // A module can arrive after the device page is already displayed. Updating
  // prototypes alone never revisits those rows when their state is unchanged.
  function scan(root) {
    for (const element of root.querySelectorAll?.("*") || []) {
      if (element.localName === "hui-number-entity-row") scheduleRow(element);
      if (element.shadowRoot) scan(element.shadowRoot);
    }
  }

  function watchElement(name, onUpdate) {
    customElements.whenDefined(name).then(() => {
      const prototype = customElements.get(name).prototype;
      if (Object.hasOwn(prototype, PATCHED)) return;
      prototype[PATCHED] = true;
      const original = prototype.updated;
      prototype.updated = function (...args) {
        const result = original?.apply(this, args);
        onUpdate(this);
        return result;
      };
      scan(document);
    });
  }

  watchElement("hui-number-entity-row", scheduleRow);
  watchElement("ha-device-entities-card", (card) => {
    Promise.resolve(card.updateComplete).then(() => {
      if (card.isConnected && card.shadowRoot) scan(card.shadowRoot);
    });
  });
  // Lifecycle callbacks are captured by the browser at customElements.define;
  // replacing connectedCallback afterwards does not hook real reconnections.
  // Navigation and card renders cover remounts without a polling timer.
  let navigationPending = false;
  const afterNavigation = () => {
    if (navigationPending) return;
    navigationPending = true;
    window.requestAnimationFrame(() => {
      navigationPending = false;
      scan(document);
    });
  };
  window.addEventListener("location-changed", afterNavigation);
  window.addEventListener("popstate", afterNavigation);
})();
