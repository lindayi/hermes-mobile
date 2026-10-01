export function createModelControls({doc, win, api, sessionId, userId, isCurrent}) {
  const element = doc.createElement('select');
  element.hidden = true;
  element.disabled = true;
  element.setAttribute('aria-label', 'Model');
  element.title = 'Model for the next message. Provider fallback may still occur.';
  let models = [], defaultModel = null;
  let applied = null, locked = false, destroyed = false;
  const current = () => !destroyed && isCurrent();
  const storageKey = `hermes:${userId}:model-controls:${JSON.stringify([userId, sessionId])}`;
  let pendingSaved=false;
  try {pendingSaved=win.sessionStorage.getItem(storageKey)!==null;} catch {}
  function validSelection(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    if (Object.keys(value).some(key => !['model', 'provider', 'reasoning_effort'].includes(key))) return false;
    const model = models.find(model => model.id === value.model && model.provider === value.provider);
    return !!model && (!Object.hasOwn(value, 'reasoning_effort') || model.reasoning_efforts.includes(value.reasoning_effort));
  }
  function persist() {
    try {
      if (applied) win.sessionStorage.setItem(storageKey, JSON.stringify(applied));
      else win.sessionStorage.removeItem(storageKey);
    } catch { /* Storage can be disabled in private browsing. */ }
  }
  function restore() {
    try {
      const raw = win.sessionStorage.getItem(storageKey);
      if (raw === null) return;
      try {
        const saved = JSON.parse(raw);
        if (validSelection(saved)) applied = saved;
      } catch { /* Malformed saved data is stale, not a selection. */ }
      if (!applied) win.sessionStorage.removeItem(storageKey);
    } catch { /* Missing browser storage is nonfatal. */ }
  }
  function validCatalog(value) {
    if (value?.available !== true || !Array.isArray(value.models) || !value.models.length) return false;
    const pairs = new Set();
    const text = value => typeof value === 'string' && value.trim().length > 0;
    return value.models.every(model => {
      if (!model || !['id', 'provider', 'label'].every(key => text(model[key]))) return false;
      const efforts = model.reasoning_efforts;
      if (!Array.isArray(efforts) || !efforts.every(text) || new Set(efforts).size !== efforts.length) return false;
      const pair = JSON.stringify([model.id, model.provider]);
      if (pairs.has(pair)) return false;
      pairs.add(pair);
      return true;
    });
  }
  const choices = new Map();
  function option(value, text, selection = null) {
    const result = doc.createElement('option');
    result.value = value;
    result.textContent = text;
    choices.set(value, {option: result, selection});
    return result;
  }
  function updateValue() {
    const choice = [...choices.values()].find(({selection}) => applied
      ? selection?.model === applied.model && selection?.provider === applied.provider && selection?.reasoning_effort === applied.reasoning_effort
      : selection === null);
    if (choice) choice.option.selected = true;
    else element.selectedIndex = -1;
  }
  async function load() {
    try {
      const value = await api.request(`/sessions/${encodeURIComponent(sessionId)}/model-options`);
      if (!current() || !validCatalog(value)) return;
      models = value.models.map(({id, provider, label, reasoning_efforts}) => ({id, provider, label, reasoning_efforts: [...reasoning_efforts]}));
      defaultModel = validSelection(value.default) ? value.default : null;
      restore();
      element.append(option('', defaultModel ? `Default — ${defaultModel.model}` : 'Default'));
      models.forEach((model, index) => {
        const label = `${model.label} (${model.provider})`;
        const selection = {model: model.id, provider: model.provider};
        element.append(option(String(index), label, selection));
        if (model.reasoning_efforts.length) {
          const group = doc.createElement('optgroup');
          group.label = `${label} — reasoning effort`;
          model.reasoning_efforts.forEach((effort, effortIndex) => group.append(option(`${index}:${effortIndex}`, `${label} — ${effort}`, {...selection, reasoning_effort: effort})));
          element.append(group);
        }
      });
      updateValue();
      pendingSaved=false;
      element.hidden = false;
      element.disabled = locked;
    } catch { /* Unknown capabilities stay hidden, including authorization failures. */ }
  }
  void load();

  function change() {
    if (!current()) return;
    if (locked || element.hidden) { updateValue(); return; }
    const choice = choices.get(element.value);
    if (!choice || element.selectedOptions.length !== 1 || element.selectedOptions[0] !== choice.option) { updateValue(); return; }
    const next = choice.selection;
    if (next && !validSelection(next)) return;
    applied = next;
    persist();
  }
  element.addEventListener('change', change);
  function setLocked(value) {
    if (destroyed) return;
    locked = !!value;
    element.disabled = locked || element.hidden;
  }
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    element.removeEventListener('change', change);
    element.hidden = true;
    element.disabled = true;
    applied = null;
    models = [];
    choices.clear();
  }
  function selection() {
    return current() && applied ? {...applied} : null;
  }
  const canSubmit=()=>current() && !pendingSaved;
  return {element, selection, setLocked, destroy, canSubmit};
}
