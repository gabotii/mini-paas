import { create } from "zustand";

import { api, onTokenChange, refreshSession, setAccessToken } from "../services/api";
import type { User } from "../types/api";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthState {
  status: AuthStatus;
  user: User | null;
  bootstrap: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  status: "loading",
  user: null,

  // On page load the access token is gone (memory only): restore it from the refresh cookie.
  bootstrap: async () => {
    const ok = await refreshSession();
    if (!ok) set({ status: "anonymous", user: null });
  },

  login: async (email, password) => {
    const session = await api.login({ email, password });
    setAccessToken(session.access_token, session.user);
  },

  register: async (name, email, password) => {
    const session = await api.register({ name, email, password });
    setAccessToken(session.access_token, session.user);
  },

  logout: async () => {
    try {
      await api.logout();
    } finally {
      setAccessToken(null);
    }
  },
}));

// Keep the store in sync with the HTTP client: login, refresh and expiry all go through here.
onTokenChange((token, user) => {
  if (token) {
    useAuth.setState((state) => ({ status: "authenticated", user: user ?? state.user }));
  } else {
    useAuth.setState({ status: "anonymous", user: null });
  }
});
