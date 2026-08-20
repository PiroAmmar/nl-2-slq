import { create } from "zustand";
import { type QueryResponse } from "../api/client";

export type Message =
  | { role: "user"; content: string }
  | { role: "assistant"; response: QueryResponse };

interface ChatState {
  messages: Message[];
  addMessage: (msg: Message) => void;
  clearMessages: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  addMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
  clearMessages: () => set({ messages: [] }),
}));
