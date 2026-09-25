import type { Identity, Role } from "../types";

const ROLES: { value: Role; label: string }[] = [
  { value: "funcional", label: "Funcional (cliente)" },
  { value: "usuario_clave", label: "Usuario clave (cliente)" },
  { value: "abap", label: "Consultor ABAP" },
  { value: "lider", label: "Líder de proyecto" },
  { value: "admin", label: "Administrador" },
];

interface Props {
  who: Identity;
  onChange: (who: Identity) => void;
}

/** Beta: elegir usuario y rol a mano para probar cada compuerta. En producción viene de Keycloak. */
export function IdentityBar({ who, onChange }: Props) {
  return (
    <div className="identity">
      <input
        aria-label="Usuario"
        value={who.user}
        onChange={(e) => onChange({ ...who, user: e.target.value })}
      />
      <select
        aria-label="Rol"
        value={who.role}
        onChange={(e) => onChange({ ...who, role: e.target.value as Role })}
      >
        {ROLES.map((r) => (
          <option key={r.value} value={r.value}>
            {r.label}
          </option>
        ))}
      </select>
    </div>
  );
}
