import { useEffect, useMemo, useState, type DragEventHandler, type KeyboardEventHandler, type ReactNode } from 'react';
import classNames from 'classnames';
import { QuickStatusTile } from '../api/client';

interface QuickStatusTileCardProps {
  tile: QuickStatusTile;
  onClick?: () => void;
  action?: ReactNode;
  draggable?: boolean;
  dragging?: boolean;
  onDragStart?: DragEventHandler<HTMLDivElement>;
  onDragOver?: DragEventHandler<HTMLDivElement>;
  onDragEnd?: DragEventHandler<HTMLDivElement>;
  onDrop?: DragEventHandler<HTMLDivElement>;
}

function getStatusClass(status: QuickStatusTile['status']): string {
  if (status === 'critical') return 'quick-status--critical';
  if (status === 'warn') return 'quick-status--warn';
  if (status === 'info') return 'quick-status--info';
  if (status === 'ok') return 'quick-status--ok';
  return 'quick-status--unknown';
}

function getStaleHintClass(status: QuickStatusTile['status']): string {
  if (status === 'critical') return 'quick-status--stale-hint-critical';
  if (status === 'warn') return 'quick-status--stale-hint-warn';
  if (status === 'info' || status === 'ok') return 'quick-status--stale-hint-ok';
  return 'quick-status--stale-hint-unknown';
}

function formatUpdatedAge(reportedAt: string | null | undefined, nowMs: number): string | null {
  if (!reportedAt) {
    return 'upd: no data';
  }
  const parsed = Date.parse(reportedAt);
  if (Number.isNaN(parsed)) {
    return null;
  }
  const elapsedSeconds = Math.max(0, Math.round((nowMs - parsed) / 1000));
  if (elapsedSeconds < 45) {
    return 'upd: just now';
  }
  if (elapsedSeconds < 3600) {
    const minutes = Math.max(1, Math.round(elapsedSeconds / 60));
    return `upd: ${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  }
  if (elapsedSeconds < 86_400) {
    const hours = Math.max(1, Math.round(elapsedSeconds / 3600));
    return `upd: ${hours} hour${hours === 1 ? '' : 's'} ago`;
  }
  const days = Math.max(1, Math.round(elapsedSeconds / 86_400));
  return `upd: ${days} day${days === 1 ? '' : 's'} ago`;
}

function renderHistorySegments(tile: QuickStatusTile) {
  if (!tile.history_loaded) {
    return (
      <div className="quick-status-history" aria-hidden="true">
        {Array.from({ length: 12 }).map((_, index) => (
          <span key={`${tile.id}:loading:${index}`} className="quick-status-history__segment quick-status--unknown" />
        ))}
      </div>
    );
  }
  if (!tile.history.length) {
    return null;
  }

  return (
    <div className="quick-status-history" aria-hidden="true">
      {tile.history.map((status, index) => (
        <span
          key={`${tile.id}:${index}`}
          className={classNames('quick-status-history__segment', getStatusClass(status))}
        />
      ))}
    </div>
  );
}

export function QuickStatusTileCard({
  tile,
  onClick,
  action,
  draggable = false,
  dragging = false,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
}: QuickStatusTileCardProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    if (!tile.show_updated_age) {
      return;
    }
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 60_000);
    return () => window.clearInterval(intervalId);
  }, [tile.show_updated_age]);

  const updatedLabel = useMemo(
    () => (tile.show_updated_age ? formatUpdatedAge(tile.reported_at, nowMs) : null),
    [nowMs, tile.reported_at, tile.show_updated_age]
  );

  const handleKeyDown: KeyboardEventHandler<HTMLDivElement> | undefined = onClick
    ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }
    : undefined;
  const tileClassName = classNames(
    'quick-status-tile',
    tile.stale ? ['quick-status--stale', getStaleHintClass(tile.status)] : getStatusClass(tile.status)
  );
  const cardClassName = classNames('quick-status-card', {
    'quick-status-card--interactive': Boolean(onClick),
    'quick-status-card--dragging': dragging,
  });
  const cardBody = (
    <>
      <div className={tileClassName}>
        {action ? <div className="quick-status-tile__action">{action}</div> : null}
        <div className="quick-status-content">
          <div className="quick-status-value">{tile.display_value}</div>
          <div className="quick-status-label">{tile.label}</div>
          {updatedLabel ? <div className="quick-status-updated">{updatedLabel}</div> : null}
        </div>
        {tile.details && tile.details.length > 0 ? <div className="quick-status-hint">Details</div> : null}
      </div>
      {renderHistorySegments(tile)}
    </>
  );

  if (onClick && !action && !draggable && !onDragStart && !onDragOver && !onDragEnd && !onDrop) {
    return (
      <button type="button" className={classNames(cardClassName, 'quick-status-card--button')} onClick={onClick}>
        {cardBody}
      </button>
    );
  }

  return (
    <div
      className={cardClassName}
      onClick={onClick}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
      onDrop={onDrop}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={handleKeyDown}
    >
      {cardBody}
    </div>
  );
}
