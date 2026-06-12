import {
  useEffect,
  useMemo,
  useState,
  type DragEventHandler,
  type KeyboardEventHandler,
  type MouseEventHandler,
  type ReactNode,
  type TouchEventHandler,
} from 'react';
import classNames from 'classnames';

import { QuickStatusTile } from '../api/client';
import { QuickStatusTileCard } from './QuickStatusTileCard';

interface QuickStatusItemCardProps {
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

function formatSeriesValue(tile: QuickStatusTile, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  if (tile.display_variant === 'temperature') {
    return `${value.toFixed(1)}°C`;
  }
  if (tile.display_variant === 'humidity') {
    return `${Math.round(value)}%`;
  }
  if (tile.display_unit?.trim()) {
    const unit = tile.display_unit.trim();
    if (unit === '%') {
      return `${Math.round(value)}%`;
    }
    if (unit === '°C') {
      return `${value.toFixed(1)}°C`;
    }
    if (unit === 'ms' || unit === 's' || unit === 'm' || unit === 'h') {
      return `${value.toFixed(1).replace(/\.0$/, '')}${unit}`;
    }
    const compact = Math.abs(value) >= 10 ? value.toFixed(1).replace(/\.0$/, '') : value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
    return `${compact} ${unit}`;
  }
  const absolute = Math.abs(value);
  if (absolute >= 100) {
    return `${Math.round(value)}`;
  }
  if (absolute >= 10) {
    return `${value.toFixed(1)}`;
  }
  return `${value.toFixed(2)}`;
}

type SparklinePoint = { x: number; y: number; rawX: number; rawY: number };
type SparklineMappedPoint = { x: number; y: number | null; rawX: number; rawY: number | null };

function buildSparkline(points: Array<{ x: number; y: number | null }>, width = 100, height = 52) {
  if (points.length === 0) {
    return {
      linePaths: [] as string[],
      areaPaths: [] as string[],
      plottedPoints: [] as SparklinePoint[],
      isolatedPoints: [] as SparklinePoint[],
    };
  }
  const minX = points[0].x;
  const maxX = points[points.length - 1].x;
  const numericPoints = points.filter((point): point is { x: number; y: number } => point.y !== null);
  if (numericPoints.length === 0) {
    return {
      linePaths: [] as string[],
      areaPaths: [] as string[],
      plottedPoints: [] as SparklinePoint[],
      isolatedPoints: [] as SparklinePoint[],
    };
  }
  const minY = Math.min(...numericPoints.map((point) => point.y));
  const maxY = Math.max(...numericPoints.map((point) => point.y));
  const xSpan = Math.max(1, maxX - minX);
  const hasVerticalVariation = maxY > minY;
  const ySpan = hasVerticalVariation ? maxY - minY : 1;
  const flatLineY = height / 2;

  const mappedPoints: SparklineMappedPoint[] = points.map((point) => ({
    rawX: point.x,
    rawY: point.y,
    x: ((point.x - minX) / xSpan) * width,
    y:
      point.y === null
        ? null
        : hasVerticalVariation
          ? height - ((point.y - minY) / ySpan) * height
          : flatLineY,
  }));

  const plottedPoints = mappedPoints.filter((point): point is SparklinePoint => point.y !== null);
  const segments: SparklinePoint[][] = [];
  let currentSegment: SparklinePoint[] = [];

  mappedPoints.forEach((point) => {
    if (point.y === null) {
      if (currentSegment.length > 0) {
        segments.push(currentSegment);
        currentSegment = [];
      }
      return;
    }
    currentSegment.push(point);
  });
  if (currentSegment.length > 0) {
    segments.push(currentSegment);
  }

  const drawableSegments = segments.filter((segment) => segment.length >= 2);
  const isolatedPoints = segments.filter((segment) => segment.length === 1).map((segment) => segment[0]);
  const linePaths = drawableSegments.map((segment) =>
    segment
      .map((point, index) => `${index === 0 ? 'M' : ' L'}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
      .join('')
  );
  const areaPaths = drawableSegments.map((segment) => {
    const linePath = segment
      .map((point, index) => `${index === 0 ? 'M' : ' L'}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
      .join('');
    const startX = segment[0].x.toFixed(2);
    const endX = segment[segment.length - 1].x.toFixed(2);
    return `${linePath} L ${endX},${height} L ${startX},${height} Z`;
  });

  return { linePaths, areaPaths, plottedPoints, isolatedPoints };
}

function findNearestPointIndex(points: SparklinePoint[], clientX: number, left: number, width: number): number | null {
  if (points.length === 0 || width <= 0) {
    return null;
  }
  const normalizedX = ((clientX - left) / width) * 100;
  let nearestIndex = 0;
  let nearestDistance = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    const distance = Math.abs(point.x - normalizedX);
    if (distance < nearestDistance) {
      nearestIndex = index;
      nearestDistance = distance;
    }
  });
  return nearestIndex;
}

function formatProbeTime(timestampMs: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(timestampMs);
}

function statusLabel(status: QuickStatusTile['status']): string {
  if (status === 'warn') return 'Warn';
  if (status === 'critical') return 'Critical';
  if (status === 'info') return 'Info';
  if (status === 'unknown') return 'Unknown';
  return 'OK';
}

function getDatumClassName(severity: 'ok' | 'info' | 'warn' | 'critical'): string {
  if (severity === 'critical') return 'quick-status-wide__datum quick-status-wide__datum--critical';
  if (severity === 'warn') return 'quick-status-wide__datum quick-status-wide__datum--warn';
  if (severity === 'info') return 'quick-status-wide__datum quick-status-wide__datum--info';
  return 'quick-status-wide__datum quick-status-wide__datum--ok';
}

export function QuickStatusItemCard({
  tile,
  onClick,
  action,
  draggable = false,
  dragging = false,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDrop,
}: QuickStatusItemCardProps) {
  const displayVariant = tile.display_variant ?? 'tile';

  const [nowMs, setNowMs] = useState(() => Date.now());
  const [activeProbeIndex, setActiveProbeIndex] = useState<number | null>(null);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 60_000);
    return () => window.clearInterval(intervalId);
  }, []);

  const updatedLabel = useMemo(
    () => (tile.show_updated_age ? formatUpdatedAge(tile.reported_at, nowMs) : '24h history'),
    [nowMs, tile.reported_at, tile.show_updated_age]
  );

  const seriesPoints = useMemo(
    () =>
      (tile.series ?? [])
        .map((point) => ({ x: Date.parse(point.recorded_at), y: point.value }))
        .filter((point) => Number.isFinite(point.x) && (point.y === null || Number.isFinite(point.y)))
        .sort((a, b) => a.x - b.x),
    [tile.series]
  );

  const sparkline = useMemo(() => buildSparkline(seriesPoints), [seriesPoints]);
  const summary = tile.series_summary ?? null;
  const activeProbePoint =
    activeProbeIndex === null || activeProbeIndex >= sparkline.plottedPoints.length
      ? null
      : sparkline.plottedPoints[activeProbeIndex];
  const statusClassName = classNames(
    'quick-status-wide',
    `quick-status-wide--${tile.display_variant}`,
    tile.stale ? ['quick-status--stale', getStaleHintClass(tile.status)] : getStatusClass(tile.status)
  );
  const cardClassName = classNames('quick-status-card', {
    'quick-status-card--interactive': Boolean(onClick),
    'quick-status-card--dragging': dragging,
  });

  const handleKeyDown: KeyboardEventHandler<HTMLDivElement> | undefined = onClick
    ? (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }
    : undefined;

  useEffect(() => {
    setActiveProbeIndex(null);
  }, [tile.id, tile.reported_at, tile.series]);

  const updateProbeFromClientX = (clientX: number, currentTarget: EventTarget & SVGSVGElement) => {
    const bounds = currentTarget.getBoundingClientRect();
    const nearestIndex = findNearestPointIndex(sparkline.plottedPoints, clientX, bounds.left, bounds.width);
    setActiveProbeIndex(nearestIndex);
  };

  const handleChartMouseMove: MouseEventHandler<SVGSVGElement> = (event) => {
    event.stopPropagation();
    updateProbeFromClientX(event.clientX, event.currentTarget);
  };

  const handleChartMouseLeave: MouseEventHandler<SVGSVGElement> = (event) => {
    setActiveProbeIndex(null);
    event.stopPropagation();
  };

  const handleChartTouchStart: TouchEventHandler<SVGSVGElement> = (event) => {
    const touch = event.touches[0];
    if (!touch) {
      return;
    }
    event.stopPropagation();
    updateProbeFromClientX(touch.clientX, event.currentTarget);
  };

  const handleChartTouchMove: TouchEventHandler<SVGSVGElement> = (event) => {
    const touch = event.touches[0];
    if (!touch) {
      return;
    }
    event.stopPropagation();
    updateProbeFromClientX(touch.clientX, event.currentTarget);
  };

  if (displayVariant === 'tile') {
    return (
      <QuickStatusTileCard
        tile={tile}
        onClick={onClick}
        action={action}
        draggable={draggable}
        dragging={dragging}
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDragEnd={onDragEnd}
        onDrop={onDrop}
      />
    );
  }

  const chartShell = (
    <div className="quick-status-wide__chart-shell">
      {activeProbePoint ? (
        <div
          className={classNames('quick-status-wide__chart-probe', {
            'quick-status-wide__chart-probe--right': activeProbePoint.x < 16,
            'quick-status-wide__chart-probe--left': activeProbePoint.x > 84,
            'quick-status-wide__chart-probe--below': activeProbePoint.y < 14,
          })}
          style={{
            left: `${activeProbePoint.x}%`,
            top: `${(activeProbePoint.y / 52) * 100}%`,
          }}
          data-testid="quick-status-chart-probe"
        >
          <span className="quick-status-wide__chart-probe-value">{formatSeriesValue(tile, activeProbePoint.rawY)}</span>
          <span className="quick-status-wide__chart-probe-time">{formatProbeTime(activeProbePoint.rawX)}</span>
        </div>
      ) : null}
      {seriesPoints.length >= 2 ? (
        <svg
          className="quick-status-wide__chart"
          viewBox="0 0 100 52"
          preserveAspectRatio="none"
          role="img"
          aria-label={`${tile.label} history chart`}
          data-testid="quick-status-chart"
          onMouseMove={handleChartMouseMove}
          onMouseLeave={handleChartMouseLeave}
          onTouchStart={handleChartTouchStart}
          onTouchMove={handleChartTouchMove}
          onClick={(event) => event.stopPropagation()}
        >
          <defs>
            <linearGradient id={`quick-status-fill-${tile.id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgba(255,255,255,0.32)" />
              <stop offset="100%" stopColor="rgba(255,255,255,0.02)" />
            </linearGradient>
          </defs>
          {sparkline.areaPaths.map((areaPath, index) => (
            <path key={`area-${index}`} d={areaPath} fill={`url(#quick-status-fill-${tile.id})`} />
          ))}
          {sparkline.linePaths.map((linePath, index) => (
            <path
              key={`line-${index}`}
              d={linePath}
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {sparkline.isolatedPoints.map((point, index) => (
            <circle
              key={`isolated-${index}`}
              cx={point.x}
              cy={point.y}
              r="1.8"
              className="quick-status-wide__chart-dot"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {activeProbePoint ? (
            <g aria-hidden="true">
              <line
                x1={activeProbePoint.x}
                y1="0"
                x2={activeProbePoint.x}
                y2="52"
                className="quick-status-wide__chart-guide"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={activeProbePoint.x}
                cy={activeProbePoint.y}
                r="2.4"
                className="quick-status-wide__chart-dot"
                vectorEffect="non-scaling-stroke"
              />
            </g>
          ) : null}
        </svg>
      ) : (
        <div className="quick-status-wide__empty">
          {tile.history_loaded ? 'Waiting for 24h history.' : 'Loading 24h history…'}
        </div>
      )}
    </div>
  );

  if (displayVariant === 'self_monitoring') {
    const highlights = tile.highlights ?? [];
    const breakdown = tile.breakdown ?? [];

    const dashboardBody = (
      <div className={statusClassName}>
        {action ? <div className="quick-status-tile__action">{action}</div> : null}
        <div className="quick-status-wide__header">
          <div className="quick-status-wide__title-wrap">
            <div className="quick-status-wide__title">{tile.label}</div>
            <div className="quick-status-wide__subtitle">{updatedLabel}</div>
          </div>
          <div className="quick-status-wide__status">{statusLabel(tile.status)}</div>
        </div>
        <div
          className={classNames('quick-status-wide__dashboard', {
            'quick-status-wide__dashboard--breakdown': breakdown.length > 0,
          })}
        >
          <div className="quick-status-wide__dashboard-main">
            <div className="quick-status-wide__dashboard-value">
              <div className="quick-status-wide__dashboard-eyebrow">{tile.backend_name}</div>
              <div className="quick-status-wide__value">{tile.display_value}</div>
              <div className="quick-status-wide__backend">
                {breakdown.length > 0 ? 'Hottest handlers in the last hour' : 'Live self-monitoring trend'}
              </div>
            </div>
            {chartShell}
          </div>
          {breakdown.length > 0 ? (
            <div className="quick-status-wide__breakdown">
              {breakdown.map((datum) => (
                <div className={getDatumClassName(datum.severity)} key={`${datum.label}-${datum.value}`}>
                  <span className="quick-status-wide__breakdown-label">{datum.label}</span>
                  <span className="quick-status-wide__breakdown-value">{datum.value}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="quick-status-wide__dashboard-stats">
              {highlights.map((datum) => (
                <div className={getDatumClassName(datum.severity)} key={`${datum.label}-${datum.value}`}>
                  <span className="quick-status-wide__datum-label">{datum.label}</span>
                  <span className="quick-status-wide__datum-value">{datum.value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        {tile.details && tile.details.length > 0 ? <div className="quick-status-hint">Details</div> : null}
      </div>
    );

    if (onClick && !action && !draggable && !onDragStart && !onDragOver && !onDragEnd && !onDrop) {
      return (
        <button type="button" className={classNames(cardClassName, 'quick-status-card--button')} onClick={onClick}>
          {dashboardBody}
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
        {dashboardBody}
      </div>
    );
  }

  const body = (
    <div className={statusClassName}>
      {action ? <div className="quick-status-tile__action">{action}</div> : null}
      <div className="quick-status-wide__header">
        <div className="quick-status-wide__title-wrap">
          <div className="quick-status-wide__title">{tile.label}</div>
          <div className="quick-status-wide__subtitle">{updatedLabel}</div>
        </div>
        <div className="quick-status-wide__status">{statusLabel(tile.status)}</div>
      </div>
      <div className="quick-status-wide__body">
        <div className="quick-status-wide__value-wrap">
          <div className="quick-status-wide__value">{tile.display_value}</div>
          <div className="quick-status-wide__backend">{tile.backend_name}</div>
        </div>
        {chartShell}
      </div>
      <div className="quick-status-wide__stats">
        <div className="quick-status-wide__stat">
          <span className="quick-status-wide__stat-label">Low</span>
          <span className="quick-status-wide__stat-value">{formatSeriesValue(tile, summary?.min_value)}</span>
        </div>
        <div className="quick-status-wide__stat">
          <span className="quick-status-wide__stat-label">High</span>
          <span className="quick-status-wide__stat-value">{formatSeriesValue(tile, summary?.max_value)}</span>
        </div>
        <div className="quick-status-wide__stat">
          <span className="quick-status-wide__stat-label">Avg</span>
          <span className="quick-status-wide__stat-value">{formatSeriesValue(tile, summary?.average_value)}</span>
        </div>
        <div className="quick-status-wide__stat">
          <span className="quick-status-wide__stat-label">24h</span>
          <span className="quick-status-wide__stat-value">
            {summary?.delta_value === null || summary?.delta_value === undefined
              ? '—'
              : `${summary.delta_value > 0 ? '+' : ''}${formatSeriesValue(tile, summary.delta_value)}`}
          </span>
        </div>
      </div>
      {tile.details && tile.details.length > 0 ? <div className="quick-status-hint">Details</div> : null}
    </div>
  );

  if (onClick && !action && !draggable && !onDragStart && !onDragOver && !onDragEnd && !onDrop) {
    return (
      <button type="button" className={classNames(cardClassName, 'quick-status-card--button')} onClick={onClick}>
        {body}
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
      {body}
    </div>
  );
}
