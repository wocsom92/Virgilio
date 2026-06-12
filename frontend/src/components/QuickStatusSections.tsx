import { QuickStatusTile } from '../api/client';
import { QuickStatusGroup } from '../hooks/useQuickStatusDashboard';
import { QuickStatusItemCard } from './QuickStatusItemCard';

interface QuickStatusSectionsProps {
  groups: QuickStatusGroup[];
  hydratingGroups: Record<string, boolean>;
  selectedTileId: number | null;
  detailLoadingId: number | null;
  onTileToggle: (tile: QuickStatusTile) => void;
  onCloseDetails: () => void;
}

function getGridItemClassName(variant: QuickStatusTile['display_variant']) {
  return variant === 'graph' || variant === 'temperature' || variant === 'humidity' || variant === 'self_monitoring'
    ? 'quick-status-grid__item quick-status-grid__item--wide'
    : 'quick-status-grid__item';
}

export function QuickStatusSections({
  groups,
  hydratingGroups,
  selectedTileId,
  detailLoadingId,
  onTileToggle,
  onCloseDetails,
}: QuickStatusSectionsProps) {
  return (
    <div className="quick-status-sections">
      {groups.map((group) => {
        const selectedTile = group.items.find((tile) => tile.id === selectedTileId) ?? null;

        return (
          <section className="quick-status-section" key={group.key}>
            <div className="quick-status-section__header">
              <div>
                <h3 className="quick-status-section__title">{group.backendName}</h3>
                <span className="quick-status-section__count">
                  {group.items.length} {group.items.length === 1 ? 'item' : 'items'}
                </span>
              </div>
              {hydratingGroups[group.key] ? (
                <span className="text-secondary small">Loading 24h history…</span>
              ) : null}
            </div>
            <div className="quick-status-grid">
              {group.items.map((tile) => (
                <div className={getGridItemClassName(tile.display_variant)} key={tile.id}>
                  <QuickStatusItemCard
                    tile={tile}
                    onClick={tile.has_details ? () => void onTileToggle(tile) : undefined}
                  />
                </div>
              ))}
            </div>
            {selectedTile ? (
              <div className="quick-status-detail card-panel rounded-4 p-3 mt-3">
                <div className="d-flex justify-content-between align-items-center gap-2 mb-2">
                  <div>
                    <div className="text-uppercase card-panel__heading fw-semibold">{selectedTile.label}</div>
                    <div className="small text-secondary">{selectedTile.backend_name}</div>
                  </div>
                  <button type="button" className="btn btn-sm btn-outline-light" onClick={onCloseDetails}>
                    Close
                  </button>
                </div>
                {detailLoadingId === selectedTile.id && (!selectedTile.details || selectedTile.details.length === 0) ? (
                  <div className="text-secondary small">Loading details…</div>
                ) : selectedTile.details && selectedTile.details.length > 0 ? (
                  <ul className="mb-0 small quick-status-detail__list">
                    {selectedTile.details.map((line) => (
                      <li
                        key={`${line.severity}:${line.text}`}
                        className={
                          line.severity === 'critical'
                            ? 'text-danger'
                            : line.severity === 'warn'
                              ? 'text-warning'
                              : 'text-success'
                        }
                      >
                        <code>{line.text}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-secondary small">No additional details are available for this tile yet.</div>
                )}
              </div>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

interface QuickStatusLoadingShellProps {
  groupNames: string[];
}

export function QuickStatusLoadingShell({ groupNames }: QuickStatusLoadingShellProps) {
  const sections = groupNames.length > 0 ? groupNames : ['Quick tiles'];

  return (
    <div className="quick-status-sections">
      {sections.map((groupName, index) => (
        <section className="quick-status-section" key={`${groupName}-${index}`}>
          <div className="quick-status-section__header">
            <div>
              <h3 className="quick-status-section__title">{groupName}</h3>
              <span className="quick-status-section__count">Loading quick tiles…</span>
            </div>
          </div>
          <div className="quick-status-grid">
            {Array.from({ length: 4 }).map((_, itemIndex) => (
              <div className="quick-status-grid__item" key={`${groupName}-${itemIndex}`}>
                <div className="card-panel rounded-4 p-3">
                  <div className="placeholder-glow d-flex flex-column gap-2">
                    <span className="placeholder col-5" />
                    <span className="placeholder col-8" />
                    <span className="placeholder col-4" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
