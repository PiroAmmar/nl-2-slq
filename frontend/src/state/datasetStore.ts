// src/state/datasetStore.ts — Zustand store for active dataset_id

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface DatasetState {
  datasetId: string | null;
  datasetName: string | null;
  setDataset: (id: string, name: string) => void;
  clearDataset: () => void;
}

export const useDatasetStore = create<DatasetState>()(
  persist(
    (set) => ({
      datasetId: null,
      datasetName: null,
      setDataset: (id, name) => set({ datasetId: id, datasetName: name }),
      clearDataset: () => set({ datasetId: null, datasetName: null }),
    }),
    { name: "nl2sql-dataset" }
  )
);
