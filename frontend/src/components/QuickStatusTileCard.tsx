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

export const QuickStatusTileCard = memo(QuickStatusTileCardComponent);
