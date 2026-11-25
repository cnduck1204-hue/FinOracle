import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

/** 模态对话框；使用原生 dialog，关闭后浏览器把焦点还给触发按钮。 */
export function Dialog({
  title,
  kicker,
  busy,
  onClose,
  children,
  wide,
  closeLabel = '关闭弹窗',
}: {
  title: string
  kicker?: string
  busy: boolean
  onClose: () => void
  children: ReactNode
  wide?: boolean
  closeLabel?: string
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
      <button
        type="button"
        className="icon-button dialog-close"
        aria-label={closeLabel}
        disabled={busy}
        onClick={onClose}
      >
        <X size={17} aria-hidden="true" />
      </button>
      {kicker && <span className="dialog-kicker">{kicker}</span>}
      <h2>{title}</h2>
      {children}
    </dialog>
  )
}
