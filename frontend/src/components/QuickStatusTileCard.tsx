import type { DragEventHandler, KeyboardEventHandler, ReactNode } from 'react';
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

function renderHistorySegments(tile: QuickStatusTile) {
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
  const handleKeyDown: KeyboardEventHandler<HTMLDivElement> | undefined = onClick
    ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }
    : undefined;
  const tileClassName = classNames('quick-status-tile', getStatusClass(tile.status));
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
