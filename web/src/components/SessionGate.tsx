import type { User, UserManager } from "oidc-client-ts";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import {
  type AuthConfig,
  createUserManager,
  fetchAuthConfig,
  headerSession,
  restoreUser,
  type Session,
  tokenSession,
} from "../auth";
import { useStored } from "../hooks";
import type { Identity, Role } from "../types";
import { IdentityBar } from "./IdentityBar";

type Render = (session: Session, bar: ReactNode) => ReactNode;

export function SessionGate({ children }: { children: Render }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAuthConfig().then(setConfig, (e: unknown) => setError(String(e)));
  }, []);

  if (error) return <p className="error center">{error}</p>;
  if (!config) return <p className="muted center">Cargando…</p>;
  return config.mode === "oidc" ? (
    <OidcSession config={config} render={children} />
  ) : (
    <HeaderSession render={children} />
  );
}

function HeaderSession({ render }: { render: Render }) {
  const [who, setWho] = useStored<Identity>("fabrica.identity", { user: "ana", role: "funcional" });
  return <>{render(headerSession(who), <IdentityBar who={who} onChange={setWho} />)}</>;
}

function OidcSession({ config, render }: { config: AuthConfig; render: Render }) {
  const manager = useMemo(() => createUserManager(config), [config]);
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [role, setRole] = useStored<Role | null>("fabrica.role", null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    restoreUser(manager).then(setUser, (e: unknown) => setError(String(e)));
    manager.events.addUserLoaded(setUser);
    manager.events.addUserSignedOut(() => setUser(null));
    return () => manager.events.removeUserLoaded(setUser);
  }, [manager]);

  useEffect(() => {
    if (!user) return;
    const probe = { user: "", role: "consultor" as Role, roles: [], headers: bearer(user) };
    api.me(probe).then(setIdentity, (e: unknown) => setError(String(e)));
  }, [user]);

  if (error) return <LoginScreen manager={manager} error={error} />;
  if (user === undefined) return <p className="muted center">Verificando sesión…</p>;
  if (user === null) return <LoginScreen manager={manager} />;
  if (!identity) return <p className="muted center">Cargando permisos…</p>;

  const session = tokenSession(user.access_token, identity, role ?? undefined);
  const bar = <RoleBar session={session} onRole={setRole} onLogout={() => manager.signoutRedirect()} />;
  return <>{render(session, bar)}</>;
}

function bearer(user: User): Record<string, string> {
  return { Authorization: `Bearer ${user.access_token}` };
}

function LoginScreen({ manager, error }: { manager: UserManager; error?: string }) {
  return (
    <div className="login">
      <h1>🏭 Fábrica SAP</h1>
      {error && <p className="error">{error}</p>}
      <button onClick={() => void manager.signinRedirect()}>Iniciar sesión</button>
    </div>
  );
}

interface RoleBarProps {
  session: Session;
  onRole: (role: Role) => void;
  onLogout: () => void;
}

function RoleBar({ session, onRole, onLogout }: RoleBarProps) {
  return (
    <div className="identity">
      <span className="user">{session.user}</span>
      <select
        aria-label="Rol"
        value={session.role}
        onChange={(e) => onRole(e.target.value as Role)}
      >
        {session.roles.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      <button className="secondary" onClick={onLogout}>
        Salir
      </button>
    </div>
  );
}
