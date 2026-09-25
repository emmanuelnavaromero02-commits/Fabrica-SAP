import { useEffect, useState } from "react";
import { api } from "../api";
import type { Session } from "../auth";
import { errorText } from "../errors";
import { usePolling } from "../hooks";
import type { BillingSnapshot, Capability, Client, Project, Stage } from "../types";
import { BarList } from "./ui/BarList";
import { useToast } from "./ui/Toasts";

const pct = (value: number) => `${Math.round(value * 100)} %`;

const STAGE_PROGRESS: Record<string, number> = {
  recepcion: 0.15,
  diseno: 0.35,
  aprobacion_cliente: 0.45,
  construccion: 0.75,
  abap_review: 0.90,
  uat: 0.95,
  cerrado: 1.0,
};

interface Props {
  session: Session;
  stages: Stage[];
  onOpen: (id: number) => void;
}

export function PortfolioView({ session, stages, onOpen }: Props) {
  const { data, error } = usePolling(() => api.portfolio(session), 10_000);
  const [tab, setTab] = useState<"metricas" | "gantt" | "gestion">("metricas");
  const [projectCode, setProjectCode] = useState("DEMO");
  const [snapshots, setSnapshots] = useState<BillingSnapshot[]>([]);

  const [clients, setClients] = useState<Client[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [caps, setCaps] = useState<Record<number, Capability[]>>({});

  const [newClientName, setNewClientName] = useState("");
  const [newClientCode, setNewClientCode] = useState("");

  const [newProjName, setNewProjName] = useState("");
  const [newProjCode, setNewProjCode] = useState("");
  const [newProjSap, setNewProjSap] = useState("MOCK-DEV");

  const [newCapName, setNewCapName] = useState("");
  const [newCapCode, setNewCapCode] = useState("");
  const [selectedProjForCap, setSelectedProjForCap] = useState<number | null>(null);

  const notify = useToast();

  useEffect(() => {
    api.snapshots(session, projectCode).then(setSnapshots, () => setSnapshots([]));
  }, [projectCode, session.user, session.role]);

  const loadHierarchy = () => {
    api.clients(session).then(setClients, () => setClients([]));
    api.projects(session).then((projs) => {
      setProjects(projs);
      if (projs.length > 0 && projs[0] && selectedProjForCap === null) {
        setSelectedProjForCap(projs[0].id);
      }
      projs.forEach((p) => {
        api.capabilities(session, p.id).then((c) => {
          setCaps((prev) => ({ ...prev, [p.id]: c }));
        }, () => {});
      });
    }, () => setProjects([]));
  };

  useEffect(() => {
    if (tab === "gestion") {
      loadHierarchy();
    }
  }, [tab, session.user, session.role]);

  const handleTakeSnapshot = async () => {
    try {
      const snap = await api.createSnapshot(session, projectCode);
      notify(`Foto de avance congelada: ${snap.hours_billed.toFixed(1)} h facturables`);
      const list = await api.snapshots(session, projectCode);
      setSnapshots(list);
    } catch (e) {
      notify(errorText(e), "error");
    }
  };

  const handleCreateClient = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newClientName || !newClientCode) return;
    try {
      await api.createClient(session, { name: newClientName, code: newClientCode.toUpperCase() });
      notify("Cliente registrado con éxito");
      setNewClientName("");
      setNewClientCode("");
      loadHierarchy();
    } catch (err) {
      notify(errorText(err), "error");
    }
  };

  const handleCreateProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProjName || !newProjCode) return;
    try {
      await api.createProject(session, {
        name: newProjName,
        code: newProjCode.toUpperCase(),
        sap_system: newProjSap,
      });
      notify("Proyecto registrado con éxito");
      setNewProjName("");
      setNewProjCode("");
      loadHierarchy();
    } catch (err) {
      notify(errorText(err), "error");
    }
  };

  const handleCreateCap = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProjForCap || !newCapName || !newCapCode) return;
    try {
      await api.createCapability(session, selectedProjForCap, {
        name: newCapName,
        code: newCapCode.toUpperCase(),
      });
      notify("Capacidad agregada");
      setNewCapName("");
      setNewCapCode("");
      loadHierarchy();
    } catch (err) {
      notify(errorText(err), "error");
    }
  };

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Cargando…</p>;

  const label = (key: string) => stages.find((s) => s.key === key)?.label ?? key;
  const byStage = stages
    .filter((s) => data.by_stage[s.key])
    .map((s) => ({ key: s.key, label: s.label, value: data.by_stage[s.key] ?? 0 }));
  const firstPass = Object.entries(data.first_pass_rate).map(([activity, rate]) => ({
    key: activity,
    label: activity,
    value: rate,
  }));
  const tiles = [
    { label: "Requisitos", value: String(data.total) },
    { label: "Agentes trabajando", value: String(data.by_state.running ?? 0) },
    { label: "Esperan decisión", value: String(data.waiting.length) },
    { label: "En pausa", value: String(data.blocked.length) },
    { label: "Ciclo promedio", value: data.lead_time_days === null ? "—" : `${data.lead_time_days} d` },
    { label: "Costo de IA", value: `$${data.ai_cost_usd.toFixed(2)}` },
  ];

  return (
    <section className="page">
      <div style={{ display: "flex", gap: "10px", marginBottom: "16px" }}>
        <button
          className={tab === "metricas" ? "" : "outline"}
          onClick={() => setTab("metricas")}
        >
          📊 Métricas y Facturación
        </button>
        <button
          className={tab === "gantt" ? "" : "outline"}
          onClick={() => setTab("gantt")}
        >
          📅 Cronograma Gantt
        </button>
        <button
          className={tab === "gestion" ? "" : "outline"}
          onClick={() => setTab("gestion")}
        >
          🏢 Clientes y Proyectos
        </button>
      </div>

      {tab === "metricas" && (
        <>
          <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              <strong>Proyecto para Facturación:</strong>
              <input
                style={{ width: "160px", padding: "4px 8px" }}
                value={projectCode}
                onChange={(e) => setProjectCode(e.target.value.toUpperCase())}
              />
            </div>
            <div className="gate-buttons">
              <button onClick={handleTakeSnapshot}>📸 Congelar Foto de Avance</button>
              <a
                className="button outline"
                href={api.exportBillingUrl(projectCode, session)}
                download
                style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", padding: "6px 12px", borderRadius: "6px" }}
              >
                📥 Descargar Excel de Facturación
              </a>
            </div>
          </div>

          <div className="tiles">
            {tiles.map((t) => (
              <div key={t.label} className="tile">
                <span className="label">{t.label}</span>
                <span className="value">{t.value}</span>
              </div>
            ))}
          </div>

          <div className="two">
            <BarList title="Requisitos por etapa" bars={byStage} />
            <BarList title="Aprobado al primer intento, por actividad" bars={firstPass} max={1} format={pct} />
          </div>

          {(data.blocked.length > 0 || data.waiting.length > 0) && (
            <div className="two">
              {[
                { title: "⛔ En pausa", rows: data.blocked },
                { title: "👤 Esperan una decisión", rows: data.waiting },
              ].map((group) => (
                <section key={group.title} className="card">
                  <h3>{group.title}</h3>
                  {group.rows.length === 0 && <p className="muted">Ninguno.</p>}
                  {group.rows.map((r) => (
                    <p key={r.id}>
                      <button className="link" onClick={() => onOpen(r.id)}>
                        #{r.id} {r.title}
                      </button>
                      <span className="muted"> · {label(r.stage)}</span>
                    </p>
                  ))}
                </section>
              ))}
            </div>
          )}

          <section className="card">
            <h3>Estimado contra trabajado</h3>
            <table className="grid">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Requisito</th>
                  <th>Etapa</th>
                  <th>Estimado</th>
                  <th>Trabajado</th>
                  <th>Costo IA</th>
                </tr>
              </thead>
              <tbody>
                {data.requirements.map((r) => (
                  <tr key={r.id}>
                    <td className="num">{r.id}</td>
                    <td>
                      <button className="link" onClick={() => onOpen(r.id)}>
                        {r.title}
                      </button>
                    </td>
                    <td>{label(r.stage)}</td>
                    <td className="num">{r.estimated_hours === null ? "—" : `${r.estimated_hours} h`}</td>
                    <td className="num">{r.worked_hours} h</td>
                    <td className="num">${r.ai_cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {snapshots.length > 0 && (
            <section className="card">
              <h3>📸 Historial de Fotos de Avance (Snapshots de Facturación)</h3>
              <table className="grid">
                <thead>
                  <tr>
                    <th># ID</th>
                    <th>Fecha</th>
                    <th>Tomada por</th>
                    <th>Horas Totales</th>
                    <th>Horas Facturables</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshots.map((s) => (
                    <tr key={s.id}>
                      <td className="num">{s.id}</td>
                      <td>{new Date(s.created_at).toLocaleString()}</td>
                      <td>{s.created_by}</td>
                      <td className="num">{s.hours_total.toFixed(1)} h</td>
                      <td className="num">{s.hours_billed.toFixed(1)} h</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}

      {tab === "gantt" && (
        <section className="card">
          <h3>📅 Cronograma Gantt de Requisitos</h3>
          <p className="muted">
            Planificación basada en estimación de horas (8 h = 1 día laboral) y porcentaje de avance por etapa.
          </p>
          <div style={{ overflowX: "auto", marginTop: "16px" }}>
            <div style={{ minWidth: "750px" }}>
              <div style={{ display: "grid", gridTemplateColumns: "240px 100px 1fr", borderBottom: "2px solid var(--border)", paddingBottom: "8px", fontWeight: "bold", fontSize: "12px", color: "var(--muted)" }}>
                <span>REQUISITO</span>
                <span>AVANCE</span>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>D1 (Inicio)</span>
                  <span>D3</span>
                  <span>D5</span>
                  <span>D8</span>
                  <span>D10+</span>
                </div>
              </div>

              {data.requirements.map((r) => {
                const hours = r.estimated_hours || 16;
                const days = Math.max(1, Math.round(hours / 8));
                const progressPct = Math.round((STAGE_PROGRESS[r.stage] ?? 0.1) * 100);
                const barWidth = Math.min(100, Math.max(15, days * 8));

                return (
                  <div
                    key={r.id}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "240px 100px 1fr",
                      alignItems: "center",
                      padding: "10px 0",
                      borderBottom: "1px solid var(--border)",
                      fontSize: "13px",
                    }}
                  >
                    <div>
                      <button className="link" onClick={() => onOpen(r.id)} style={{ fontWeight: 600 }}>
                        #{r.id} {r.title.slice(0, 24)}…
                      </button>
                      <div className="muted" style={{ fontSize: "11px" }}>
                        {label(r.stage)} · {hours}h ({days}d)
                      </div>
                    </div>
                    <div>
                      <span className="chip" style={{ fontSize: "11px" }}>{progressPct}%</span>
                    </div>
                    <div style={{ background: "rgba(100, 116, 139, 0.1)", borderRadius: "4px", height: "24px", position: "relative", overflow: "hidden" }}>
                      <div
                        style={{
                          width: `${barWidth}%`,
                          height: "100%",
                          background: "rgba(2, 132, 199, 0.25)",
                          border: "1px solid #0284c7",
                          borderRadius: "4px",
                          position: "relative",
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            width: `${progressPct}%`,
                            height: "100%",
                            background: "#0284c7",
                            transition: "width 0.3s ease",
                          }}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>
      )}

      {tab === "gestion" && (
        <div className="two">
          <section className="card">
            <h3>🏢 Registrar Nuevo Cliente</h3>
            <form onSubmit={handleCreateClient} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <label>
                Nombre del Cliente:
                <input value={newClientName} onChange={(e) => setNewClientName(e.target.value)} required placeholder="Ej. Corporativo Retail" />
              </label>
              <label>
                Código Identificador:
                <input value={newClientCode} onChange={(e) => setNewClientCode(e.target.value)} required placeholder="Ej. RET-CL" />
              </label>
              <button type="submit">Guardar Cliente</button>
            </form>

            <h4 style={{ marginTop: "24px" }}>Clientes Registrados</h4>
            {clients.length === 0 ? (
              <p className="muted">No hay clientes dados de alta.</p>
            ) : (
              <table className="grid">
                <thead>
                  <tr><th>#</th><th>Código</th><th>Nombre</th></tr>
                </thead>
                <tbody>
                  {clients.map((c) => (
                    <tr key={c.id}>
                      <td className="num">{c.id}</td>
                      <td><strong>{c.code}</strong></td>
                      <td>{c.name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="card">
            <h3>📁 Registrar Nuevo Proyecto</h3>
            <form onSubmit={handleCreateProject} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <label>
                Nombre del Proyecto:
                <input value={newProjName} onChange={(e) => setNewProjName(e.target.value)} required placeholder="Ej. Migración Clean Core" />
              </label>
              <label>
                Código del Proyecto:
                <input value={newProjCode} onChange={(e) => setNewProjCode(e.target.value)} required placeholder="Ej. RISE-2026" />
              </label>
              <label>
                Sistema SAP Conectado:
                <select value={newProjSap} onChange={(e) => setNewProjSap(e.target.value)}>
                  <option value="MOCK-DEV">MOCK-DEV (Simulado / Clean Core Autónomo)</option>
                  <option value="S4H-DEV">S4H-DEV (SAP RISE S/4HANA ADT)</option>
                </select>
              </label>
              <button type="submit">Guardar Proyecto</button>
            </form>

            <h4 style={{ marginTop: "24px" }}>Proyectos y Dominios / Capacidades</h4>
            {projects.length === 0 ? (
              <p className="muted">No hay proyectos dados de alta.</p>
            ) : (
              <div>
                <table className="grid">
                  <thead>
                    <tr><th>#</th><th>Código</th><th>Nombre</th><th>Sistema SAP</th><th>Módulos / Capacidades</th></tr>
                  </thead>
                  <tbody>
                    {projects.map((p) => (
                      <tr key={p.id}>
                        <td className="num">{p.id}</td>
                        <td><strong>{p.code}</strong></td>
                        <td>{p.name}</td>
                        <td><span className="chip">{p.sap_system || "MOCK-DEV"}</span></td>
                        <td>
                          {caps[p.id]?.length ? (
                            (caps[p.id] ?? []).map((c) => (
                              <span key={c.id} className="chip" style={{ marginRight: "4px" }}>
                                {c.code}
                              </span>
                            ))
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div style={{ marginTop: "16px", padding: "12px", border: "1px dashed var(--border)", borderRadius: "6px" }}>
                  <h5>+ Agregar Capacidad / Módulo a Proyecto</h5>
                  <form onSubmit={handleCreateCap} style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "8px" }}>
                    <select
                      value={selectedProjForCap ?? ""}
                      onChange={(e) => setSelectedProjForCap(Number(e.target.value))}
                    >
                      {projects.map((p) => (
                        <option key={p.id} value={p.id}>{p.code} - {p.name}</option>
                      ))}
                    </select>
                    <input
                      style={{ width: "80px" }}
                      placeholder="Cód (FI)"
                      value={newCapCode}
                      onChange={(e) => setNewCapCode(e.target.value)}
                    />
                    <input
                      placeholder="Nombre (Finanzas)"
                      value={newCapName}
                      onChange={(e) => setNewCapName(e.target.value)}
                    />
                    <button type="submit">Agregar</button>
                  </form>
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
