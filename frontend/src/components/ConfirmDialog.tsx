import { useEffect, useId, useRef, useState } from 'react'
import { Icon } from './Icon'

export function ConfirmDialog({ title, description, confirmLabel, reasonRequired = false, busy = false, error = '', onClose, onConfirm }: {
  title: string; description: string; confirmLabel: string; reasonRequired?: boolean;
  busy?: boolean; error?: string; onClose: () => void; onConfirm: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const titleId = useId()
  const descriptionId = useId()
  const dialog = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    dialog.current?.querySelector<HTMLElement>('textarea, button')?.focus()
    return () => previous?.focus()
  }, [])
  return <div className="modal-backdrop" onMouseDown={event => { if (!busy && event.target === event.currentTarget) onClose() }}>
    <div className="modal confirmation-modal" ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} onKeyDown={event => {
      if (event.key === 'Escape' && !busy) { event.stopPropagation(); onClose() }
      if (event.key === 'Tab') {
        const controls = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), textarea') || [])]
        const first = controls[0]; const last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }}>
      <div className="modal-header"><div><h2 id={titleId}>{title}</h2><p id={descriptionId}>{description}</p></div><button className="icon-button" aria-label="关闭确认" disabled={busy} onClick={onClose}><Icon name="close"/></button></div>
      {(reasonRequired || error) && <div className="confirmation-body">{reasonRequired && <label className="field"><span>忽略原因 *</span><textarea aria-label="忽略原因" autoFocus rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder="说明暂不跟进此信号的原因"/></label>}{error && <p className="form-error" role="alert">{error}</p>}</div>}
      <div className="modal-footer"><button className="button button-secondary" disabled={busy} onClick={onClose}>取消</button><button className="button button-danger" disabled={busy || (reasonRequired && !reason.trim())} onClick={() => onConfirm(reason.trim())}>{busy ? '正在处理…' : confirmLabel}</button></div>
    </div>
  </div>
}
