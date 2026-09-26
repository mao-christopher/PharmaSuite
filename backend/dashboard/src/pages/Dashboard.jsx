import React from 'react';
import CameraFeed from '../components/CameraFeed';
import Notifications from '../components/Notifications';
import ActiveBottles from '../components/ActiveBottles';
import ActivityLog from '../components/ActivityLog';
import Prescriptions from '../components/Prescriptions';
import StockSummary from '../components/StockSummary';

export default function Dashboard() {
  return (
    <div className="dash-grid">
      <div className="stack">
        <CameraFeed />
        <ActivityLog />
        <StockSummary />
      </div>
      <div className="stack">
        <Notifications />
        <ActiveBottles />
        <Prescriptions />
      </div>
    </div>
  );
}
