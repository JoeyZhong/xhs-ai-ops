import { create } from "zustand";
import { persist } from "zustand/middleware";

interface GoalsState {
  activeGoalId: string;
  activeGoalName: string;
  activePersonaId: string;
  setActiveGoal: (id: string, name: string) => void;
  setActiveGoalId: (id: string) => void;
  setActivePersonaId: (id: string) => void;
  reset: () => void;
}

export const GOALS_STORAGE_KEY = "spider-xhs-goals";

const DEFAULT_GOALS_STATE = {
  activeGoalId: "",
  activeGoalName: "",
  activePersonaId: "default",
};

export const useGoalsStore = create<GoalsState>()(
  persist(
    (set) => ({
      ...DEFAULT_GOALS_STATE,
      setActiveGoal: (id, name) => set({ activeGoalId: id, activeGoalName: name }),
      setActiveGoalId: (id) => set({ activeGoalId: id }),
      setActivePersonaId: (id) => set({ activePersonaId: id }),
      reset: () => set(DEFAULT_GOALS_STATE),
    }),
    { name: GOALS_STORAGE_KEY }
  )
);

export function resetGoalsStore(): void {
  useGoalsStore.getState().reset();
  useGoalsStore.persist.clearStorage();
}
