import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { Check, ChevronDown } from 'lucide-react'

export interface SelectOption<T extends string = string> {
  value: T
  label: string
  /** Optional secondary count or metadata shown right-aligned inside the option. */
  meta?: string
  /** Optional hierarchy depth (0, 1, 2, ...) for tree-structured selects like folders. */
  depth?: number
  /** Optional leading icon rendered in the option and trigger. */
  icon?: ReactNode
  disabled?: boolean
}

export interface SelectProps<T extends string = string> {
  id?: string
  name?: string
  value: T
  options: readonly SelectOption<T>[]
  onChange: (value: T) => void
  placeholder?: string
  disabled?: boolean
  size?: 'md' | 'sm'
  /** Size to content instead of filling parent width (e.g. inline toolbar controls). */
  inline?: boolean
  ariaLabel?: string
  className?: string
}

function findNextEnabled<T extends string>(
  options: readonly SelectOption<T>[],
  startIndex: number,
  direction: 1 | -1,
): number {
  const count = options.length
  if (!count) return -1
  let index = startIndex
  for (let step = 0; step < count; step++) {
    index = (index + direction + count) % count
    if (!options[index].disabled) return index
  }
  return -1
}

function findEdgeEnabled<T extends string>(
  options: readonly SelectOption<T>[],
  fromEnd: boolean,
): number {
  if (fromEnd) {
    for (let i = options.length - 1; i >= 0; i--) {
      if (!options[i].disabled) return i
    }
    return -1
  }
  for (let i = 0; i < options.length; i++) {
    if (!options[i].disabled) return i
  }
  return -1
}

export function Select<T extends string = string>({
  id,
  name,
  value,
  options,
  onChange,
  placeholder = '请选择',
  disabled = false,
  size = 'md',
  inline = false,
  ariaLabel,
  className,
}: SelectProps<T>) {
  const generatedId = useId()
  const triggerId = id ?? `${generatedId}-trigger`
  const listboxId = `${generatedId}-listbox`

  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const typeaheadRef = useRef({ buffer: '', timer: 0 as number | undefined })

  const [open, setOpen] = useState(false)
  const [highlighted, setHighlighted] = useState(-1)
  const [menuStyle, setMenuStyle] = useState<CSSProperties>({})
  const [placement, setPlacement] = useState<'bottom' | 'top'>('bottom')

  const selectedIndex = useMemo(
    () => options.findIndex((option) => option.value === value),
    [options, value],
  )
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : undefined

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false)
    if (restoreFocus) {
      triggerRef.current?.focus()
    }
  }, [])

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const gap = 4
    const viewportMargin = 10
    const maxPreferredHeight = 272
    const spaceBelow = window.innerHeight - rect.bottom - viewportMargin - gap
    const spaceAbove = rect.top - viewportMargin - gap
    const openAbove = spaceBelow < Math.min(maxPreferredHeight, 168) && spaceAbove > spaceBelow
    const availableHeight = Math.max(
      112,
      Math.min(maxPreferredHeight, openAbove ? spaceAbove : spaceBelow),
    )
    const minWidth = Math.max(rect.width, inline ? 148 : rect.width)
    const maxWidth = Math.min(Math.max(minWidth, 340), window.innerWidth - viewportMargin * 2)
    const left = Math.max(
      viewportMargin,
      Math.min(rect.left, window.innerWidth - minWidth - viewportMargin),
    )

    setPlacement(openAbove ? 'top' : 'bottom')
    setMenuStyle({
      left: `${left}px`,
      minWidth: `${minWidth}px`,
      maxWidth: `${maxWidth}px`,
      maxHeight: `${availableHeight}px`,
      ...(openAbove
        ? { bottom: `${window.innerHeight - rect.top + gap}px`, top: 'auto' }
        : { top: `${rect.bottom + gap}px`, bottom: 'auto' }),
    })
  }, [inline])

  const openMenu = useCallback(
    (initialIndex?: number) => {
      if (disabled) return
      const targetIndex =
        initialIndex !== undefined && initialIndex >= 0 && !options[initialIndex]?.disabled
          ? initialIndex
          : selectedIndex >= 0 && !options[selectedIndex]?.disabled
            ? selectedIndex
            : findEdgeEnabled(options, false)
      setHighlighted(targetIndex)
      setOpen(true)
    },
    [disabled, options, selectedIndex],
  )

  // Synchronize top-layer popover state so dropdowns never clip inside <dialog> or overflow containers.
  useLayoutEffect(() => {
    const menu = menuRef.current
    if (!menu) return
    if (open) {
      updatePosition()
      if (typeof menu.showPopover === 'function') {
        try {
          if (!menu.matches(':popover-open')) menu.showPopover()
        } catch {
          // Ignore if already open or unsupported in test environment.
        }
      }
    } else if (typeof menu.hidePopover === 'function') {
      try {
        if (menu.matches(':popover-open')) menu.hidePopover()
      } catch {
        // Ignore if already closed.
      }
    }
  }, [open, updatePosition])

  useEffect(() => {
    if (!open) return
    if (disabled) {
      setOpen(false)
      return
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return
      setOpen(false)
    }
    const onScroll = (event: Event) => {
      const target = event.target as Node | null
      if (target && menuRef.current?.contains(target)) return
      updatePosition()
    }
    const onResize = () => updatePosition()

    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open, disabled, updatePosition])

  useEffect(() => {
    if (!open || highlighted < 0) return
    const optionEl = menuRef.current?.querySelector<HTMLElement>(
      `[data-option-index="${highlighted}"]`,
    )
    optionEl?.scrollIntoView({ block: 'nearest' })
  }, [open, highlighted])

  useEffect(() => {
    return () => {
      if (typeaheadRef.current.timer !== undefined) {
        window.clearTimeout(typeaheadRef.current.timer)
      }
    }
  }, [])

  function commitIndex(index: number) {
    const option = options[index]
    if (!option || option.disabled) return
    if (option.value !== value) onChange(option.value)
    closeMenu(true)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return

    if (event.key === 'Escape') {
      if (open) {
        event.preventDefault()
        event.stopPropagation()
        closeMenu(true)
      }
      return
    }

    if (event.key === 'Tab') {
      if (open) closeMenu(false)
      return
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      if (!open) {
        openMenu()
      } else {
        setHighlighted((prev) => findNextEnabled(options, prev < 0 ? -1 : prev, 1))
      }
      return
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        openMenu()
      } else {
        setHighlighted((prev) => findNextEnabled(options, prev < 0 ? options.length : prev, -1))
      }
      return
    }

    if (event.key === 'Home') {
      if (!open) return
      event.preventDefault()
      setHighlighted(findEdgeEnabled(options, false))
      return
    }

    if (event.key === 'End') {
      if (!open) return
      event.preventDefault()
      setHighlighted(findEdgeEnabled(options, true))
      return
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (!open) {
        openMenu()
      } else if (highlighted >= 0) {
        commitIndex(highlighted)
      }
      return
    }

    // Printable character typeahead
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const nextBuffer = (typeaheadRef.current.buffer + event.key).toLowerCase()
      typeaheadRef.current.buffer = nextBuffer
      if (typeaheadRef.current.timer !== undefined) {
        window.clearTimeout(typeaheadRef.current.timer)
      }
      typeaheadRef.current.timer = window.setTimeout(() => {
        typeaheadRef.current.buffer = ''
      }, 500)

      const matchIndex = options.findIndex(
        (opt) => !opt.disabled && opt.label.toLowerCase().includes(nextBuffer),
      )
      if (matchIndex >= 0) {
        if (!open) openMenu(matchIndex)
        else setHighlighted(matchIndex)
      }
    }
  }

  const activeDescendant = open && highlighted >= 0 ? `${listboxId}-opt-${highlighted}` : undefined

  return (
    <div
      className={`ui-select${size === 'sm' ? ' ui-select-sm' : ''}${inline ? ' ui-select-inline' : ''}${className ? ` ${className}` : ''}`}
    >
      {name && <input type="hidden" name={name} value={value} />}
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        role="combobox"
        className="ui-select-trigger"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={activeDescendant}
        data-open={open || undefined}
        data-value={value}
        onClick={() => (open ? closeMenu(false) : openMenu())}
        onKeyDown={handleKeyDown}
      >
        <span className="ui-select-value">
          {selectedOption?.icon && (
            <span className="ui-select-icon" aria-hidden="true">
              {selectedOption.icon}
            </span>
          )}
          <span className={`ui-select-text${selectedOption ? '' : ' is-placeholder'}`}>
            {selectedOption?.label ?? placeholder}
          </span>
        </span>
        <ChevronDown size={14} className="ui-select-chevron" aria-hidden="true" />
      </button>

      <div
        ref={menuRef}
        popover="manual"
        className={`ui-select-menu${size === 'sm' ? ' ui-select-menu-sm' : ''}`}
        data-open={open || undefined}
        data-placement={placement}
        style={menuStyle}
      >
        {open && (
          <ul id={listboxId} role="listbox" className="ui-select-list" aria-label={ariaLabel}>
            {options.map((option, index) => {
              const isSelected = option.value === value
              const isHighlighted = index === highlighted
              const depth = option.depth ?? 0
              return (
                <li
                  key={option.value || `__empty_${index}`}
                  id={`${listboxId}-opt-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={option.disabled || undefined}
                  data-option-index={index}
                  data-highlighted={isHighlighted || undefined}
                  className="ui-select-option"
                  style={
                    depth > 0 ? { paddingLeft: `${12 + Math.min(depth, 5) * 16}px` } : undefined
                  }
                  onMouseEnter={() => {
                    if (!option.disabled) setHighlighted(index)
                  }}
                  onMouseDown={(event) => {
                    // Keep focus on trigger button until click commits selection.
                    event.preventDefault()
                  }}
                  onClick={() => commitIndex(index)}
                >
                  {depth > 0 && (
                    <span className="ui-select-branch" aria-hidden="true">
                      └
                    </span>
                  )}
                  {option.icon && (
                    <span className="ui-select-icon" aria-hidden="true">
                      {option.icon}
                    </span>
                  )}
                  <span className="ui-select-option-label" title={option.label}>
                    {option.label}
                  </span>
                  {option.meta && <span className="ui-select-option-meta">{option.meta}</span>}
                  {isSelected && <Check size={14} className="ui-select-check" aria-hidden="true" />}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
