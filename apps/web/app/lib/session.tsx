"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, apiStore } from "./api";
import type { SessionInfo, UserRecord, WorkspaceSummary } from "./types";

const WORKSPACE_KEY = "cermat.workspace";

type SessionState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "ready"; user: UserRecord; workspaces: WorkspaceSummary[]; workspace: WorkspaceSummary | null };

type SessionApi = {
  state: SessionState;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: { email: string; password: string; display_name: string; workspace_name?: string }) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  selectWorkspace: (id: string) => void;
  openDemo: () => Promise<WorkspaceSummary>;
};

const SessionContext = createContext<SessionApi | null>(null);

function remembered(): string | null {
  try {
    return window.localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

function remember(id: string | null) {
  try {
    if (id) window.localStorage.setItem(WORKSPACE_KEY, id);
    else window.localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    // storage unavailable (private mode) — the default workspace is used instead
  }
}

function choose(workspaces: WorkspaceSummary[], preferred: string | null): WorkspaceSummary | null {
  return (
    workspaces.find((w) => w.id === preferred) ??
    workspaces.find((w) => !w.is_demo) ??
    workspaces[0] ??
    null
  );
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  const adopt = useCallback((info: SessionInfo, preferred: string | null = remembered()) => {
    const workspace = choose(info.workspaces, preferred);
    apiStore.csrf = info.csrf_token;
    apiStore.workspaceId = workspace?.id ?? null;
    remember(workspace?.id ?? null);
    setState({ status: "ready", user: info.user, workspaces: info.workspaces, workspace });
  }, []);

  const becomeAnonymous = useCallback(() => {
    apiStore.csrf = null;
    apiStore.workspaceId = null;
    setState({ status: "anonymous" });
  }, []);

  const refresh = useCallback(async () => {
    try {
      adopt(await api<SessionInfo>("/api/v1/auth/me"));
    } catch {
      becomeAnonymous();
    }
  }, [adopt, becomeAnonymous]);

  useEffect(() => {
    apiStore.onUnauthorized = becomeAnonymous;
    let active = true;
    api<SessionInfo>("/api/v1/auth/me")
      .then((info) => active && adopt(info))
      .catch(() => active && becomeAnonymous());
    return () => {
      active = false;
      apiStore.onUnauthorized = null;
    };
  }, [adopt, becomeAnonymous]);

  const value = useMemo<SessionApi>(
    () => ({
      state,
      refresh,
      async signIn(email, password) {
        adopt(await api<SessionInfo>("/api/v1/auth/signin", { method: "POST", json: { email, password } }));
      },
      async signUp(input) {
        adopt(await api<SessionInfo>("/api/v1/auth/signup", { method: "POST", json: input }), null);
      },
      async signOut() {
        try {
          await api("/api/v1/auth/signout", { method: "POST" });
        } finally {
          remember(null);
          becomeAnonymous();
        }
      },
      selectWorkspace(id) {
        if (state.status !== "ready") return;
        const workspace = state.workspaces.find((w) => w.id === id) ?? null;
        apiStore.workspaceId = workspace?.id ?? null;
        remember(workspace?.id ?? null);
        setState({ ...state, workspace });
      },
      async openDemo() {
        const demo = await api<WorkspaceSummary>("/api/v1/workspaces/demo", { method: "POST" });
        const info = await api<SessionInfo>("/api/v1/auth/me");
        adopt(info, demo.id);
        return demo;
      },
    }),
    [state, refresh, adopt, becomeAnonymous]
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionApi {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside <SessionProvider>");
  return context;
}
