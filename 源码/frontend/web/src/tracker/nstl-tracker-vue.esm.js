import NstlTracker from './nstl-tracker.esm.js';

function normalizeLogValue(value) {
  if (value == null) {
    return '{}';
  }
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch (error) {
    return '{}';
  }
}

function bindLog(el, binding) {
  if (!el || !el.setAttribute) {
    return;
  }
  const value = binding && Object.prototype.hasOwnProperty.call(binding, 'value') ? binding.value : binding;
  el.setAttribute('data-log', normalizeLogValue(value));
}

const NstlTrackerVue = {
  install(appOrVue, options) {
    const tracker = NstlTracker.init(options || {});

    if (appOrVue.config && appOrVue.config.globalProperties) {
      appOrVue.config.globalProperties.$tracker = tracker;
    } else if (appOrVue.prototype) {
      appOrVue.prototype.$tracker = tracker;
    }

    if (typeof appOrVue.directive === 'function') {
      appOrVue.directive('log', {
        bind: bindLog,
        inserted: bindLog,
        update: bindLog,
        mounted: bindLog,
        updated: bindLog
      });
    }

    return tracker;
  }
};

export default NstlTrackerVue;
