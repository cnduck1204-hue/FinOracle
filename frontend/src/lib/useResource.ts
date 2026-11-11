import { useCallback, useEffect, useRef, useState } from 'react'

interface ResourceState<T> {
  data: T | null
  error: unknown
  loading: boolean
}

/**
 * 加载一份数据，deps 变化时清空重载。pollWhile 为真时定时刷新：
 * 刷新期间保留旧数据，页面不闪烁；短暂失败后继续轮询。
 */
export function useResource<T>(
  load: () => Promise<T>,
  deps: readonly unknown[],
  pollWhile?: (value: T) => boolean,
  intervalMs = 2500,
) {
  const [state, setState] = useState<ResourceState<T>>({ data: null, error: null, loading: true })
  const [revision, setRevision] = useState(0)
  const callbacks = useRef({ load, pollWhile })
  callbacks.current = { load, pollWhile }
  const lastData = useRef<T | null>(null)

  useEffect(() => {
    lastData.current = null
    setState({ data: null, error: null, loading: true })
  }, deps)

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      const poll = callbacks.current.pollWhile
      if (lastData.current !== null && poll?.(lastData.current))
        timer = setTimeout(() => setRevision((value) => value + 1), intervalMs)
    }
    callbacks.current
      .load()
      .then((data) => {
        if (!active) return
        lastData.current = data
        setState({ data, error: null, loading: false })
        schedule()
      })
      .catch((error: unknown) => {
        if (!active) return
        setState((previous) => ({ ...previous, error, loading: false }))
        schedule()
      })
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [...deps, revision, intervalMs])

  const reload = useCallback(() => setRevision((value) => value + 1), [])
  return { ...state, reload }
}
