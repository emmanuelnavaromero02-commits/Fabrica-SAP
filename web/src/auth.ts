import { type User, UserManager, WebStorageStateStore } from "oidc-client-ts";

import type { Identity, Role } from "./types";

export interface AuthConfig {
  mode: "headers" | "oidc";
  issuer: string;
  client_id: string;
}

export interface Session {
  user: string;
  role: Role;
  roles: Role[];
  headers: Record<string, string>;
}

export async function fetchAuthConfig(): Promise<AuthConfig> {
  const response = await fetch("/api/auth/config");
  if (!response.ok) throw new Error(`No se pudo leer la configuración de acceso (${response.status})`);
  return (await response.json()) as AuthConfig;
}

export function createUserManager(config: AuthConfig): UserManager {
  const origin = window.location.origin;
  return new UserManager({
    authority: config.issuer,
    client_id: config.client_id,
    redirect_uri: `${origin}/`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: "openid profile email",
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
}

export async function restoreUser(manager: UserManager): Promise<User | null> {
  const params = new URLSearchParams(window.location.search);
  if (params.has("code") && params.has("state")) {
    const user = await manager.signinRedirectCallback();
    window.history.replaceState({}, document.title, window.location.pathname);
    return user;
  }
  const user = await manager.getUser();
  return user && !user.expired ? user : null;
}

export function headerSession(who: Identity): Session {
  return {
    user: who.user,
    role: who.role,
    roles: [who.role],
    headers: { "X-Fabrica-User": who.user, "X-Fabrica-Role": who.role },
  };
}

export function tokenSession(accessToken: string, identity: Identity, role?: Role): Session {
  const roles = identity.roles ?? [identity.role];
  const active = role && roles.includes(role) ? role : identity.role;
  return {
    user: identity.user,
    role: active,
    roles,
    headers: { Authorization: `Bearer ${accessToken}`, "X-Fabrica-Role": active },
  };
}
