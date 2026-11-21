import { useEffect, useRef, type ReactNode } from 'react'

/** 模态对话框；使用原生 dialog，关闭后浏览器把焦点还给触发按钮。 */
export function Dialog({
  title,
  busy,
  onClose,
  children,
  wide,
}: {
  title: string
  busy: boolean
  onClose: () => void
  children: ReactNode
  wide?: boolean
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const element = dialog.current
    element?.showModal()
    return () => element?.close()
  }, [])
  return (
    <dialog
      ref={dialog}
      className={`admin-confirm ${wide ? 'dialog-wide' : ''}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) onClose()
      }}
    >
      <h2>{title}</h2>
      {children}
    </dialog>
  )
}
