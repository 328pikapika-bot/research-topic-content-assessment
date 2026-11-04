/** Stream first, with an initial read and a watchdog for connections that go silent. */
export function followTaskProgress<T>(options: {
  subscribe: (onChange: (detail: T) => void, onError: () => void) => (() => void) | Promise<() => void>
  fetch: () => Promise<T>
  isLive: (detail: T) => boolean
  onChange: (detail: T) => void
  onSyncing: (syncing: boolean) => void
  version?: (detail: T) => number
}) {
  let disposed = false, revision = 0, fetching = false
  let lastStreamAt: number | undefined, lastVersion = -Infinity, disconnected = false
  let timer: ReturnType<typeof setInterval> | undefined
  let unsubscribe: (() => void) | undefined
  const stopPolling = () => { if (timer !== undefined) clearInterval(timer); timer = undefined }
  const stop = () => { disposed = true; revision++; stopPolling(); unsubscribe?.(); options.onSyncing(false) }
  const apply = (detail: T) => {
    const version = options.version?.(detail)
    if (version != null && Number.isFinite(version)) {
      if (version < lastVersion) return
      lastVersion = version
    }
    options.onChange(detail)
    if (!options.isLive(detail)) stop()
  }
  const onChange = (detail: T) => {
    if (disposed) return
    revision++; lastStreamAt = Date.now(); disconnected = false
    options.onSyncing(false); apply(detail)
  }
  const synchronize = async () => {
    if (disposed || fetching) return
    fetching = true
    const requestedRevision = revision
    try {
      const detail = await options.fetch()
      if (!disposed && requestedRevision === revision) apply(detail)
    } catch { /* Keep results; the next watchdog tick retries. */ }
    finally { fetching = false }
  }
  const onError = () => { if (!disposed) { disconnected = true; options.onSyncing(true) } }
  timer = setInterval(() => {
    if (disposed || (!disconnected && lastStreamAt != null && Date.now() - lastStreamAt < 5000)) return
    options.onSyncing(true)
    return synchronize()
  }, 5000)
  try {
    Promise.resolve(options.subscribe(onChange, onError)).then(cleanup => {
      if (disposed) cleanup()
      else unsubscribe = cleanup
    }, onError)
  } catch { onError() }
  // A newly created task may still carry its queued response. Do not wait for
  // an EventSource connection or its first event to read material results.
  void synchronize()
  return stop
}
