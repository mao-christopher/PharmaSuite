import React, { useEffect, useId, useRef } from 'react';
import { XIcon } from '@phosphor-icons/react';

export function Card({ title, subtitle, actions, children, footer, flush = false, className = '', ...rest }) {
  return (
    <section className={`card ${className}`} {...rest}>
      {(title || actions) && (
        <header className="card-header">
          <div className="card-heading">
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className={flush ? 'card-body flush' : 'card-body'}>{children}</div>
      {footer && <footer className="card-footer">{footer}</footer>}
    </section>
  );
}

export function Badge({ tone = 'gray', children, title }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Empty({ children }) {
  return <p className="empty">{children}</p>;
}

export function EmptyState({ icon: Icon, title, children, actions }) {
  return (
    <div className="empty-state">
      {Icon && (
        <span className="empty-state-icon" aria-hidden="true">
          <Icon size={20} />
        </span>
      )}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {actions && <div className="empty-state-actions">{actions}</div>}
    </div>
  );
}

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="page-header">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

export function Metrics({ children, label }) {
  return (
    <section className="metrics" aria-label={label}>
      {children}
    </section>
  );
}

export function Metric({ label, value, hint, tone }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className={`metric-value ${tone ? `text-${tone}` : ''}`}>{value}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

const FOCUSABLE = 'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [href]';

export function Dialog({ title, onClose, children, footer, width = 520 }) {
  const ref = useRef(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement;
    const body = ref.current?.querySelector('.dialog-body');
    const first = body?.querySelector(FOCUSABLE);
    (first || ref.current)?.focus({ preventScroll: true });
    return () => previous?.focus?.({ preventScroll: true });
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      if (e.key !== 'Tab' || !ref.current) return;
      const items = [...ref.current.querySelectorAll(FOCUSABLE)];
      if (items.length === 0) return;
      const [first, last] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        className="dialog"
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="dialog-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close dialog">
            <XIcon size={16} aria-hidden="true" />
          </button>
        </header>
        <div className="dialog-body">{children}</div>
        {footer && <footer className="dialog-footer">{footer}</footer>}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel, busyLabel, busy, error, destructive, onConfirm, onClose, secondaryLabel, onSecondary }) {
  return (
    <Dialog
      title={title}
      onClose={onClose}
      width={460}
      footer={
        <>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          {onSecondary && (
            <button type="button" className="btn" onClick={onSecondary} disabled={busy}>
              {secondaryLabel}
            </button>
          )}
          <button
            type="button"
            className={`btn ${destructive ? 'btn-danger-solid' : 'btn-primary'}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? busyLabel || 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="form">
        <div className="lead">{children}</div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}
