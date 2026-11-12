export function createModelControls({doc, win, api, sessionId, userId, isCurrent}) {
  const element = doc.createElement('select');
  const reasoningElement = doc.createElement('select');
  element.hidden = reasoningElement.hidden = true;
  element.disabled = reasoningElement.disabled = true;
  element.setAttribute('aria-label', 'Model');
  reasoningElement.setAttribute('aria-label', 'Reasoning effort');
  element.title = 'Requested model for the next message. Explicit choices do not use fallback.';
  reasoningElement.title = 'Reasoning effort for the next message.';
  let models = [], defaultModel = null, appliedModel = null, appliedEffort = null;
  let locked = false, destroyed = false;
  const current = () => !destroyed && isCurrent();
  const storageKey = `hermes:${userId}:model-controls:${JSON.stringify([userId, sessionId])}`;
  function readSaved() {
    let raw = null;
    try { raw = win.localStorage.getItem(storageKey); } catch {}
    if (raw === null) {
      try { raw = win.sessionStorage.getItem(storageKey); } catch {}
      if (raw !== null) {
        // Copy even stale choices: losing one must not authorize Default.
        try { win.localStorage.setItem(storageKey, raw); } catch { return raw; }
      }
    }
    if (raw !== null) {
      try { win.sessionStorage.removeItem(storageKey); } catch {}
    }
    return raw;
  }
  const initialSaved = readSaved();
  let pendingSaved = initialSaved !== null && initialSaved !== '{}';

  function modelFor(selection) {
    return models.find(model => model.id === selection?.model
      && model.provider === selection?.provider) || null;
  }
  function selectedModel() {
    return appliedModel || defaultModel;
  }
  function validSaved(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const keys = Object.keys(value);
    if (!keys.length) return true; // Explicit Default also suppresses legacy migration.
    if (keys.some(key => !['model', 'provider', 'reasoning_effort'].includes(key))) return false;
    const hasModel = Object.hasOwn(value, 'model'), hasProvider = Object.hasOwn(value, 'provider');
    if (hasModel !== hasProvider || (!hasModel && !Object.hasOwn(value, 'reasoning_effort'))) return false;
    if (hasModel) {
      if (typeof value.model !== 'string' || typeof value.provider !== 'string'
          || !modelFor(value)) return false;
    }
    if (Object.hasOwn(value, 'reasoning_effort')) {
      const active = hasModel ? modelFor(value) : defaultModel;
      if (typeof value.reasoning_effort !== 'string'
          || !active?.reasoning_efforts.includes(value.reasoning_effort)) return false;
    }
    return true;
  }
  function persist() {
    const saved = {};
    if (appliedModel) Object.assign(saved, {model: appliedModel.id, provider: appliedModel.provider});
    if (appliedEffort) saved.reasoning_effort = appliedEffort;
    try {
      win.localStorage.setItem(storageKey, JSON.stringify(saved));
      win.sessionStorage.removeItem(storageKey);
    } catch { /* Storage can be disabled in private browsing. */ }
  }
  function restore() {
    try {
      const raw = readSaved();
      if (raw === null) { pendingSaved = false; return; }
      pendingSaved = true;
      try {
        const saved = JSON.parse(raw);
        if (validSaved(saved)) {
          appliedModel = Object.hasOwn(saved, 'model') ? modelFor(saved) : null;
          appliedEffort = saved.reasoning_effort || null;
          pendingSaved = false;
        }
      } catch { /* Malformed saved data is stale, not a selection. */ }
      // Keep stale data until an explicit choice, including across reentry.
    } catch { /* Missing browser storage is nonfatal. */ }
  }
  function validCatalog(value) {
    if (value?.available !== true || !Array.isArray(value.models) || !value.models.length) return false;
    const pairs = new Set();
    const text = value => typeof value === 'string' && value.trim().length > 0;
    const validModel = model => !!model
      && ['id', 'provider', 'label'].every(key => text(model[key]))
      && Array.isArray(model.reasoning_efforts)
      && model.reasoning_efforts.every(text)
      && new Set(model.reasoning_efforts).size === model.reasoning_efforts.length;
    if (!value.models.every(validModel)) return false;
    for (const model of value.models) {
      const pair = JSON.stringify([model.id, model.provider]);
      if (pairs.has(pair)) return false;
      pairs.add(pair);
    }
    if (value.default === null) return true;
    return !!value.default && typeof value.default === 'object' && !Array.isArray(value.default)
      && Object.keys(value.default).length === 2
      && text(value.default.model) && text(value.default.provider)
      && value.models.some(model => model.id === value.default.model
        && model.provider === value.default.provider);
  }

  const modelChoices = new Map(), reasoningChoices = new Map();
  function addOption(select, choices, value, text, item = null) {
    const option = doc.createElement('option');
    option.value = value;
    option.textContent = text;
    choices.set(value, {option, item});
    select.append(option);
  }
  function renderModel() {
    modelChoices.clear();
    element.replaceChildren();
    addOption(element, modelChoices, '',
      defaultModel ? `Default — ${defaultModel.id}` : 'Default');
    models.forEach((model, index) => addOption(
      element, modelChoices, String(index), `${model.label} (${model.provider})`, model));
    const choice = [...modelChoices.values()].find(({item}) => appliedModel
      ? item?.id === appliedModel.id && item?.provider === appliedModel.provider
      : item === null);
    if (choice) choice.option.selected = true;
    if (pendingSaved) {
      const placeholder = doc.createElement('option');
      placeholder.textContent = 'Saved choice unavailable — choose model';
      placeholder.value = 'stale';
      placeholder.disabled = true;
      element.prepend(placeholder);
      placeholder.selected = true;
    }
  }
  function renderReasoning() {
    reasoningChoices.clear();
    reasoningElement.replaceChildren();
    const model = selectedModel();
    const efforts = model?.reasoning_efforts || [];
    addOption(reasoningElement, reasoningChoices, '', 'Default');
    efforts.forEach(effort => addOption(reasoningElement, reasoningChoices, effort, effort, effort));
    reasoningElement.hidden = pendingSaved || !efforts.length;
    reasoningElement.disabled = locked || reasoningElement.hidden;
    reasoningElement.value = appliedEffort && efforts.includes(appliedEffort) ? appliedEffort : '';
  }
  async function load() {
    try {
      const value = await api.request(`/sessions/${encodeURIComponent(sessionId)}/model-options`);
      if (!current() || !validCatalog(value)) return;
      models = value.models.map(({id, provider, label, reasoning_efforts}) => ({
        id, provider, label, reasoning_efforts: [...reasoning_efforts]}));
      defaultModel = value.default
        ? models.find(model => model.id === value.default.model
          && model.provider === value.default.provider) || null
        : null;
      restore();
      renderModel();
      renderReasoning();
      element.hidden = false;
      element.disabled = locked;
    } catch { /* Unknown capabilities stay hidden, including authorization failures. */ }
  }
  void load();

  function changeModel() {
    if (!current()) return;
    if (locked || element.hidden) { renderModel(); return; }
    const choice = modelChoices.get(element.value);
    if (!choice || element.selectedOptions.length !== 1
        || element.selectedOptions[0] !== choice.option) { renderModel(); return; }
    const next = choice.item;
    if (pendingSaved || next?.id !== appliedModel?.id || next?.provider !== appliedModel?.provider) {
      pendingSaved = false;
      appliedModel = next;
      appliedEffort = null;
      persist();
      renderModel();
      renderReasoning();
    }
  }
  function changeReasoning() {
    if (!current()) return;
    if (locked || reasoningElement.hidden) { renderReasoning(); return; }
    const choice = reasoningChoices.get(reasoningElement.value);
    if (!choice || reasoningElement.selectedOptions.length !== 1
        || reasoningElement.selectedOptions[0] !== choice.option) { renderReasoning(); return; }
    appliedEffort = choice.item;
    persist();
  }
  element.addEventListener('change', changeModel);
  reasoningElement.addEventListener('change', changeReasoning);

  function setLocked(value) {
    if (destroyed) return;
    locked = !!value;
    element.disabled = locked || element.hidden;
    reasoningElement.disabled = locked || reasoningElement.hidden;
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    element.removeEventListener('change', changeModel);
    reasoningElement.removeEventListener('change', changeReasoning);
    element.hidden = reasoningElement.hidden = true;
    element.disabled = reasoningElement.disabled = true;
    appliedModel = null;
    appliedEffort = null;
    models = [];
    modelChoices.clear();
    reasoningChoices.clear();
  }
  function selection() {
    if (!current() || (!appliedModel && !appliedEffort)) return null;
    const model = appliedModel || defaultModel;
    if (!model) return null;
    return {...{model: model.id, provider: model.provider},
      ...(appliedEffort ? {reasoning_effort: appliedEffort} : {})};
  }
  const canSubmit = () => current() && !pendingSaved;
  return {element, reasoningElement, selection, setLocked, destroy, canSubmit};
}
