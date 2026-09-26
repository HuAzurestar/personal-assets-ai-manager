// A read-only UI refresh loop, never a business scheduler or retry command.
export function startVisiblePoll({ load, apply, onState, isAlive = () => true,
  canPoll = () => true, documentRef = document, timers = window, interval = 5000 }) {
  let stopped = false;
  let busy = false;
  let timer;
  let controller;
  let generation = 0;
  let lastSuccess = null;
  const valid = (ticket) => !stopped && ticket === generation && !documentRef.hidden && isAlive();
  const schedule = () => {
    timers.clearTimeout(timer);
    if (!stopped && !documentRef.hidden && isAlive()) timer = timers.setTimeout(tick, interval);
  };
  const report = (state) => onState?.({ state, lastSuccess });
  const tick = async () => {
    if (stopped || documentRef.hidden || !isAlive()) return;
    if (busy || !canPoll()) { schedule(); return; }
    busy = true;
    const ticket = ++generation;
    controller = new AbortController();
    const timeout = timers.setTimeout(() => controller?.abort(), 10000);
    try {
      const result = await load(controller.signal);
      if (valid(ticket) && canPoll()) {
        apply(result);
        lastSuccess = new Date().toISOString();
        report("CURRENT");
      }
    } catch (_error) {
      if (valid(ticket)) report("UNKNOWN");
    } finally {
      timers.clearTimeout(timeout);
      busy = false;
      schedule();
    }
  };
  const visibility = () => {
    ++generation;
    controller?.abort();
    timers.clearTimeout(timer);
    if (documentRef.hidden) report("PAUSED");
    else { report("REFRESHING"); void tick(); }
  };
  documentRef.addEventListener("visibilitychange", visibility);
  report(documentRef.hidden ? "PAUSED" : "REFRESHING");
  schedule();
  return () => {
    stopped = true;
    ++generation;
    controller?.abort();
    timers.clearTimeout(timer);
    documentRef.removeEventListener("visibilitychange", visibility);
  };
}
