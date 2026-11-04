import './nstl-tracker.core.js';

const NstlTracker = window.NstlTracker;

export const init = (options) => NstlTracker.init(options);
export const create = (options) => NstlTracker.create(options);
export const getInstance = () => NstlTracker.getInstance();

export default NstlTracker;
