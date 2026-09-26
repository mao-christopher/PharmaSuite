import React, { useState, useEffect } from 'react';
import Header from './components/Header';
import VideoPlayer from './components/VideoPlayer';
import InventoryBoard from './components/InventoryBoard';
import AlertCenter from './components/AlertCenter';
import PrescriptionPanel from './components/PrescriptionPanel';
import DisposalModal from './components/DisposalModal';

export default function App() {
  const [data, setData] = useState({
    scenario: 'demo_scenario_01',
    media_time_ms: 0,
    is_playing: false,
    inventory: {},
    sessions: {},
    disposals: {},
    alerts: {},
    receipts: [],
    transactions: {},
  });
  const [isConnected, setIsConnected] = useState(false);
  const [activeDisposal, setActiveDisposal] = useState(null);

  const fetchState = async () => {
    try {
      const res = await fetch('/api/inventory');
      if (res.ok) {
        const json = await res.json();
        setData(json);

        // Check if pending disposal modal should open
        const pending = Object.values(json.disposals || {}).find(d => d.status === 'pending_employee_entry');
        if (pending) {
          setActiveDisposal(pending);
        }
      }
    } catch (err) {
      console.error('Failed to fetch inventory state:', err);
    }
  };

  useEffect(() => {
    fetchState();

    // WebSocket live stream connection
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost = window.location.hostname === 'localhost' ? 'localhost:8000' : window.location.host;
    const ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/events`);

    ws.onopen = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onmessage = (evt) => {
      try {
        const snapshot = JSON.parse(evt.data);
        if (snapshot.type === 'state_snapshot') {
          setData(prev => ({
            ...prev,
            media_time_ms: snapshot.media_time_ms,
            is_playing: snapshot.is_playing,
            inventory: snapshot.inventory || prev.inventory,
            sessions: snapshot.sessions || prev.sessions,
            disposals: snapshot.disposals || prev.disposals,
            alerts: snapshot.alerts || prev.alerts,
          }));

          const pending = Object.values(snapshot.disposals || {}).find(d => d.status === 'pending_employee_entry');
          if (pending) {
            setActiveDisposal(pending);
          }
        }
      } catch (err) {
        console.error('Error parsing WS message:', err);
      }
    };

    return () => ws.close();
  }, []);

  const handleTogglePlay = async () => {
    const action = data.is_playing ? 'pause' : 'play';
    await fetch('/api/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    fetchState();
  };

  const handleRestart = async () => {
    await fetch('/api/replay/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'restart' }),
    });
    fetchState();
  };

  const handleResolveAlert = async (alertId) => {
    await fetch(`/api/inventory/confirmations/${alertId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    fetchState();
  };

  const handleUpdateTxStatus = async (txId, status) => {
    await fetch(`/api/transactions/${txId}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    fetchState();
  };

  const handleSubmitDisposal = async (disposalId, receiptId, explicitQty) => {
    await fetch(`/api/inventory/disposals/${disposalId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        selected_receipt_id: receiptId,
        explicit_quantity: explicitQty,
      }),
    });
    setActiveDisposal(null);
    fetchState();
  };

  return (
    <div className="dashboard-container">
      <Header
        scenarioName={data.scenario}
        isPlaying={data.is_playing}
        onTogglePlay={handleTogglePlay}
        onRestart={handleRestart}
        isConnected={isConnected}
      />

      <InventoryBoard inventory={data.inventory} />

      <div className="main-grid">
        <div className="video-column">
          <VideoPlayer mediaTimeMs={data.media_time_ms} scenarioName={data.scenario} />
        </div>

        <div className="sidebar-column">
          <AlertCenter alerts={data.alerts} onResolveAlert={handleResolveAlert} />
          <PrescriptionPanel transactions={data.transactions} onUpdateStatus={handleUpdateTxStatus} />
        </div>
      </div>

      {activeDisposal && (
        <DisposalModal
          disposal={activeDisposal}
          receipts={data.receipts || []}
          onSubmit={handleSubmitDisposal}
          onClose={() => setActiveDisposal(null)}
        />
      )}
    </div>
  );
}
