export type LockRequest = (
  name: string,
  callback: (available: boolean) => Promise<void>,
) => Promise<void>

export function browserLockRequest(): LockRequest | undefined {
  if (typeof window === 'undefined' || !navigator.locks) return undefined
  return (name, callback) =>
    navigator.locks.request(name, { ifAvailable: true }, (lock) => callback(Boolean(lock)))
}

/** A copied sessionStorage token must never be redeemed by two live tabs. */
export class TabLease {
  private current: { marker: string; flight: Promise<boolean>; release: () => void } | null = null
  constructor(private request?: LockRequest) {}
  get enabled() {
    return Boolean(this.request)
  }

  claim(marker: string): Promise<boolean> {
    if (!this.request) return Promise.resolve(true)
    if (this.current?.marker === marker) return this.current.flight
    this.release()
    let release!: () => void
    const held = new Promise<void>((resolve) => {
      release = resolve
    })
    const flight = new Promise<boolean>((resolve, reject) => {
      void this.request!(`finoracle.session-owner:${marker}`, async (available) => {
        resolve(available)
        if (available) await held
      }).catch(reject)
    })
    this.current = { marker, flight, release }
    return flight
  }

  release() {
    this.current?.release()
    this.current = null
  }
}
