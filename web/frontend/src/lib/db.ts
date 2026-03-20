import localforage from 'localforage';

export const sessionsStore = localforage.createInstance({
  name: 'AIMinutes',
  storeName: 'sessions',
});

export const segmentsStore = localforage.createInstance({
  name: 'AIMinutes',
  storeName: 'segments',
});

export const documentsStore = localforage.createInstance({
  name: 'AIMinutes',
  storeName: 'documents',
});
