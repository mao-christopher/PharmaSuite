/** Owns the listeners and in-flight connection for one selected BLE wristband. */
export class BandLink {
  constructor({ service, characteristic, onNotification, onDisconnected }) {
    this.service = service;
    this.characteristicId = characteristic;
    this.onNotification = onNotification;
    this.onDisconnected = onDisconnected;
    this.selected = null;
    this.events = null;
    this.valueHandler = null;
    this.disconnectHandler = null;
    this.connectTask = null;
    this.generation = 0;
    this.closed = false;
  }

  detach() {
    if (this.events && this.valueHandler) {
      this.events.removeEventListener('characteristicvaluechanged', this.valueHandler);
    }
    if (this.selected && this.disconnectHandler) {
      this.selected.removeEventListener('gattserverdisconnected', this.disconnectHandler);
    }
    this.events = null;
    this.valueHandler = null;
    this.disconnectHandler = null;
  }

  connect(selected) {
    if (this.closed) return Promise.reject(new Error('Wristband connection is closed'));
    if (this.connectTask?.selected === selected) return this.connectTask.promise;
    if (this.selected === selected && this.events && selected.gatt.connected) return Promise.resolve(true);

    const generation = ++this.generation;
    const previous = this.selected;
    this.detach();
    this.selected = selected;
    if (previous && previous !== selected) previous.gatt?.disconnect();

    const promise = this.open(selected, generation);
    this.connectTask = { selected, promise };
    // Use both outcomes so a failed connection does not create an unhandled rejection.
    promise.then(
      () => { if (this.connectTask?.promise === promise) this.connectTask = null; },
      () => { if (this.connectTask?.promise === promise) this.connectTask = null; },
    );
    return promise;
  }

  async open(selected, generation) {
    const current = () => !this.closed && this.selected === selected && this.generation === generation;
    const active = () => {
      if (!current()) {
        // An older asynchronous connect may complete after a different band was chosen.
        if (this.selected !== selected || this.closed) selected.gatt?.disconnect();
        return false;
      }
      return selected.gatt.connected;
    };
    const bandId = selected.name?.match(/^Wristband-([A-Za-z0-9]{2})$/)?.[1];
    if (!bandId) throw new Error('Selected device is not a configured Wristband-XX');

    this.disconnectHandler = () => {
      if (!current()) return;
      this.detach();
      this.connectTask = null;
      this.onDisconnected(selected);
    };
    selected.addEventListener('gattserverdisconnected', this.disconnectHandler);
    try {
      const server = await selected.gatt.connect();
      if (!active()) return false;
      const service = await server.getPrimaryService(this.service);
      if (!active()) return false;
      const events = await service.getCharacteristic(this.characteristicId);
      if (!active()) return false;
      this.events = events;
      this.valueHandler = (event) => {
        if (current() && selected.gatt.connected) this.onNotification(event, bandId);
      };
      events.addEventListener('characteristicvaluechanged', this.valueHandler);
      await events.startNotifications();
      return active();
    } catch (error) {
      if (current()) this.detach();
      throw error;
    }
  }

  close() {
    this.closed = true;
    this.generation += 1;
    this.detach();
    this.selected?.gatt?.disconnect();
    this.selected = null;
  }
}
