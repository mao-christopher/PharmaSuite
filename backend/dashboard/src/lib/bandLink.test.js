import assert from 'node:assert/strict';
import test from 'node:test';
import { BandLink } from './bandLink.js';

class FakeTarget {
  listeners = new Map();

  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
  }

  removeEventListener(type, callback) {
    this.listeners.get(type)?.delete(callback);
  }

  emit(type) {
    for (const callback of [...(this.listeners.get(type) || [])]) callback({ target: this });
  }

  count(type) {
    return this.listeners.get(type)?.size || 0;
  }
}

function fakeBand(name = 'Wristband-01') {
  const selected = new FakeTarget();
  const events = new FakeTarget();
  selected.name = name;
  events.starts = 0;
  events.startNotifications = async () => { events.starts += 1; };
  const service = { getCharacteristic: async () => events };
  const server = { getPrimaryService: async () => service };
  selected.gatt = {
    connected: false,
    connects: 0,
    async connect() {
      this.connects += 1;
      this.connected = true;
      return server;
    },
    disconnect() {
      if (!this.connected) return;
      this.connected = false;
      selected.emit('gattserverdisconnected');
    },
  };
  return { selected, events, server };
}

function link(onNotification = () => {}, onDisconnected = () => {}) {
  return new BandLink({ service: 'service', characteristic: 'event', onNotification, onDisconnected });
}

test('concurrent and repeated connects keep one notification and disconnect listener', async () => {
  const { selected, events } = fakeBand();
  const received = [];
  const connection = link((event, id) => received.push(id));
  const first = connection.connect(selected);
  const second = connection.connect(selected);
  assert.equal(first, second);
  assert.equal(await first, true);
  assert.equal(await connection.connect(selected), true);
  assert.equal(selected.gatt.connects, 1);
  assert.equal(events.starts, 1);
  assert.equal(events.count('characteristicvaluechanged'), 1);
  assert.equal(selected.count('gattserverdisconnected'), 1);
  events.emit('characteristicvaluechanged');
  assert.deepEqual(received, ['01']);
});

test('disconnect and reconnect replace listeners without duplicate notifications', async () => {
  const { selected, events } = fakeBand();
  const received = [];
  const disconnected = [];
  const connection = link((event, id) => received.push(id), (device) => disconnected.push(device));
  await connection.connect(selected);
  selected.gatt.disconnect();
  assert.deepEqual(disconnected, [selected]);
  assert.equal(events.count('characteristicvaluechanged'), 0);
  assert.equal(selected.count('gattserverdisconnected'), 0);
  assert.equal(await connection.connect(selected), true);
  assert.equal(events.count('characteristicvaluechanged'), 1);
  assert.equal(selected.count('gattserverdisconnected'), 1);
  events.emit('characteristicvaluechanged');
  assert.deepEqual(received, ['01']);
});

test('switching bands ignores an older connection that finishes later', async () => {
  const old = fakeBand('Wristband-01');
  const next = fakeBand('Wristband-02');
  let finishOld;
  old.selected.gatt.connect = () => new Promise((resolve) => {
    finishOld = () => { old.selected.gatt.connected = true; resolve(old.server); };
  });
  const received = [];
  const connection = link((event, id) => received.push(id));
  const oldTask = connection.connect(old.selected);
  assert.equal(await connection.connect(next.selected), true);
  finishOld();
  assert.equal(await oldTask, false);
  assert.equal(old.selected.gatt.connected, false);
  assert.equal(old.events.count('characteristicvaluechanged'), 0);
  assert.equal(old.selected.count('gattserverdisconnected'), 0);
  next.events.emit('characteristicvaluechanged');
  assert.deepEqual(received, ['02']);
});

test('a disconnect during notification setup permits a fresh reconnect', async () => {
  const { selected, events } = fakeBand();
  const finishNotifications = [];
  events.startNotifications = () => new Promise((resolve) => { finishNotifications.push(resolve); });
  const connection = link();
  const first = connection.connect(selected);
  await new Promise(setImmediate);
  assert.equal(finishNotifications.length, 1);
  selected.gatt.disconnect();
  const second = connection.connect(selected);
  assert.notEqual(first, second);
  await new Promise(setImmediate);
  assert.equal(finishNotifications.length, 2);
  finishNotifications.forEach((finish) => finish());
  assert.equal(await first, false);
  assert.equal(await second, true);
  assert.equal(events.count('characteristicvaluechanged'), 1);
});

test('closing the page removes listeners and prevents later reconnects', async () => {
  const { selected, events } = fakeBand();
  const connection = link();
  await connection.connect(selected);
  connection.close();
  assert.equal(events.count('characteristicvaluechanged'), 0);
  assert.equal(selected.count('gattserverdisconnected'), 0);
  assert.equal(selected.gatt.connected, false);
  await assert.rejects(connection.connect(selected), /closed/);
});
