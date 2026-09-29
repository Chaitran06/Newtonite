import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { Me } from "./types";

interface AuthValue {
  user: Me | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue>(null as unknown as AuthValue);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<Me | null>(() => {
    const raw = localStorage.getItem("opsdesk.user");
    return raw && getToken() ? (JSON.parse(raw) as Me) : null;
  });

  const logout = useCallback(() => {
    setToken(null);
    localStorage.removeItem("opsdesk.user");
    setUser(null);
    qc.clear(); // never leak one user's cached data to the next login
  }, [qc]);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  // Refresh memberships/roles from the server on load (roles may have changed since last visit).
  useEffect(() => {
    if (!getToken()) return;
    api<Me>("/me")
      .then((me) => {
        setUser(me);
        localStorage.setItem("opsdesk.user", JSON.stringify(me));
      })
      .catch(() => {});
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api<{ access_token: string; user: Me }>("/auth/login", {
        method: "POST",
        body: { email, password },
      });
      qc.clear();
      setToken(res.access_token);
      localStorage.setItem("opsdesk.user", JSON.stringify(res.user));
      setUser(res.user);
    },
    [qc],
  );

  return <AuthContext.Provider value={{ user, login, logout }}>{children}</AuthContext.Provider>;
}
