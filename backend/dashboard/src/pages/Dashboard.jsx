import React from 'react';
import { useLive } from '../lib/live';
import { formatNumber, inventoryTotals } from '../lib/format';
import { Metric, Metrics } from '../components/ui';
import CameraFeed from '../components/CameraFeed';
import FloorMap from '../components/FloorMap';
import Notifications from '../components/Notifications';
import ActiveBottles from '../components/ActiveBottles';
import ActivityLog from '../components/ActivityLog';
import Prescriptions from '../components/Prescriptions';
import StockSummary from '../components/StockSummary';
import LiveFeed from '../components/LiveFeed';
import LiveMovements from '../components/LiveMovements';
import { useLiveCapture } from '../lib/liveCapture';

export default function Dashboard() {
  const { state } = useLive();
  const capture = useLiveCapture();
  const t = inventoryTotals(state);
  const live = capture.camera !== 'off';
  const showLive = live && capture.panel === 'live';

  return (
    <>
      <h1 className="sr-only">Dashboard</h1>
      <Metrics label="Pharmacy status">
        <Metric label="Needs attention" value={t.attention} tone={t.attention ? 'red' : undefined} hint="Open alerts and disposals" />
        <Metric label="Bottles in pharmacy" value={formatNumber(t.bottles)} hint={`${formatNumber(t.onShelf)} on shelves`} />
        <Metric label="Off shelf" value={t.offShelf} hint="In hand or at the counter" />
        <Metric label="Expired batches" value={t.expired} tone={t.expired ? 'red' : undefined} hint="Still waiting for disposal" />
      </Metrics>
      <div className="dash-grid">
        <div className="stack">
          {showLive ? <LiveFeed /> : <CameraFeed />}
          {(live || state.live?.movements?.length > 0) && <LiveMovements />}
          <FloorMap />
          {!showLive && <ActivityLog />}
          <StockSummary />
        </div>
        <div className="stack">
          <Notifications />
          <ActiveBottles />
          <Prescriptions />
        </div>
      </div>
    </>
  );
}
