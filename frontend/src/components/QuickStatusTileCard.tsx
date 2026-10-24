import { memo, useMemo, type DragEventHandler, type KeyboardEventHandler, type ReactNode } from 'react';
import classNames from 'classnames';
import { QuickStatusTile } from '../api/client';
import { useNow } from '../contexts/NowContext';
import { formatUpdatedAge, getStaleHintClass, getStatusClass } from '../utils/formatting';

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

export function quickStatusLabel(status: QuickStatusTile['status'], stale?: boolean): string {
  if (stale) return 'Stale';
  if (status === 'warn') return 'Warning';
  if (status === 'critical') return 'Critical';
  if (status === 'info') return 'Info';
  if (status === 'unknown') return 'No data';
  return 'OK';
}

export function renderHistorySegments(tile: QuickStatusTile) {
  if (!tile.history_loaded) {
    return (
      <div className="quick-status-history quick-status-history--loading" aria-hidden="true">
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
    <div className="quick-status-history" aria-hidden="true" title="Last 24 hours">
      {tile.history.map((status, index) => (
        <span
          key={`${tile.id}:${index}`}
          className={classNames('quick-status-history__segment', getStatusClass(status))}
        />
      ))}
    </div>
  );
}

function QuickStatusTileCardComponent({
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
  const nowMs = useNow();

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
  const statusText = quickStatusLabel(tile.status, tile.stale);
  const tileClassName = classNames(
    'quick-status-tile',
    tile.stale ? ['quick-status--stale', getStaleHintClass(tile.status)] : getStatusClass(tile.status)
  );
  const cardClassName = classNames('quick-status-card', {
    'quick-status-card--interactive': Boolean(onClick),
    'quick-status-card--dragging': dragging,
  });
  const hasDetails = Boolean(tile.details && tile.details.length > 0);
  const cardBody = (
    <div className={tileClassName}>
      {action ? <div className="quick-status-tile__action">{action}</div> : null}
      <div className="quick-status-tile__head">
        <span className="quick-status-label">{tile.label}</span>
        <span className="quick-status-tile__status" title={statusText}>
          <span className="quick-status-tile__dot" aria-hidden="true" />
          <span className="visually-hidden">{statusText}</span>
        </span>
      </div>
      <div className="quick-status-content">
        <div className="quick-status-value">{tile.display_value}</div>
      </div>
      {updatedLabel || hasDetails ? (
        <div className="quick-status-tile__foot">
          {updatedLabel ? <span className="quick-status-updated">{updatedLabel}</span> : <span />}
          {hasDetails ? <span className="quick-status-hint">Details</span> : null}
        </div>
      ) : null}
      {renderHistorySegments(tile)}
    </div>
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

export const QuickStatusTileCard = memo(QuickStatusTileCardComponent);
