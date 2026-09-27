const DB_NAME = 'pharma-live-events';

function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('events', { keyPath: 'event_id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function operation(mode, callback) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('events', mode);
      const request = callback(tx.objectStore('events'));
      tx.oncomplete = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

export const savePending = (event) => operation('readwrite', (store) => store.put(event));
export const removePending = (id) => operation('readwrite', (store) => store.delete(id));
export const listPending = () => operation('readonly', (store) => store.getAll());
