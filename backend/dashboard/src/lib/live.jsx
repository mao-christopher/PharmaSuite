import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { request } from './api';

const LiveContext = createContext(null);

export function LiveProvider({ children }) {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      setState(await request('/api/inventory'));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    let ws;
    let retry;
    let closed = false;
    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${window.location.host}/ws/events`);
      ws.onopen = () => {
        setConnected(true);
        refresh();
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 2000);
      };
      ws.onmessage = (evt) => {
        const { type, ...msg } = JSON.parse(evt.data);
        if (type === 'state_snapshot') setState(msg);
        else if (type === 'clock') setState((prev) => (prev ? { ...prev, ...msg } : prev));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [refresh]);

  const actions = useMemo(() => {
    const post = async (url, body = {}) => {
      const result = await request(url, { method: 'POST', body });
      await refresh();
      return result;
    };
    const id = encodeURIComponent;
    return {
      refresh,
      control: (action) => post('/api/replay/control', { action }),
      loadRecording: (name) => post(`/api/recordings/${id(name)}/load`),
      applyRecording: (name) => post(`/api/recordings/${id(name)}/apply`),
      deleteRecording: async (name) => {
        const result = await request(`/api/recordings/${id(name)}`, { method: 'DELETE' });
        await refresh();
        return result;
      },
      resetInventory: () => post('/api/inventory/reset'),
      resolveAlert: (alertId) => post(`/api/inventory/confirmations/${id(alertId)}`),
      confirmLocation: (alertId, regionId) =>
        post(`/api/inventory/confirmations/${id(alertId)}`, { resolved_region_id: regionId }),
      receiveStock: (body) => post('/api/inventory/receipts', body),
      setTransactionStatus: (txId, status) => post(`/api/transactions/${id(txId)}/status`, { status }),
      submitDisposal: (disposalId, receiptId, quantity) =>
        post(`/api/inventory/disposals/${id(disposalId)}`, {
          selected_receipt_id: receiptId,
          explicit_quantity: quantity,
        }),
    };
  }, [refresh]);

  const value = useMemo(() => ({ state, connected, error, ...actions }), [state, connected, error, actions]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive() {
  return useContext(LiveContext);
}
