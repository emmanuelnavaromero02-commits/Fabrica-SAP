# Crea el usuario y rol de servicio de la fabrica (Fabro) con la frontera
# estructural del brief (seccion 8.4):
#
#   - Estados del brief (creados si faltan).
#   - El tipo 'Requisito' acepta esos estados.
#   - Rol "Agente Fabro" con permisos operativos minimos (sin admin, sin
#     delete, sin gestion de miembros).
#   - Workflows del tipo 'Requisito' para el rol Fabro: SOLO las transiciones
#     que el orquestador necesita; NUNCA hacia estados de aprobacion.
#     Asi, si por bug o por intento el agente trata de aprobar, OpenProject
#     lo rechaza por permisos. Brief 8.4: "la frontera es estructural".
#   - Workflows del tipo 'Requisito' para el rol Member: transiciones humanas
#     (disparar la fabrica y soltar las compuertas).
#   - Usuario 'fabro-agent' miembro de todos los proyectos con ese rol.
#   - Tambien anade :add_work_package_comments al rol 'Agente MCP' (asi el
#     componente MCP puede comentar).
#   - Token de API del usuario fabro-agent.
#
# Idempotente: re-ejecutarlo no duplica nada.

login    = 'fabro-agent'
role_nm  = 'Agente Fabro'
password = ENV['FABRO_AGENT_PASSWORD'].to_s
abort 'FALTA FABRO_AGENT_PASSWORD' if password.empty?

# Nombres del brief (secciones 8 y 9). Si EPI-USE renombra, ajustar aqui y
# en orquestador/.env (claves OP_STATUS_*).
OPERATIONAL = ['Asignado a la fabrica', 'En revision', 'En desarrollo',
               'Lista para pruebas', 'Esperando clarificacion']
APPROVAL    = ['Aprobacion funcional', 'VoBo del cliente', 'Aprobacion del desarrollo']
ALL_NEW     = OPERATIONAL + APPROVAL

# --- 1. Estados ---
status_id = {}
ALL_NEW.each do |name|
  s = Status.find_by(name: name) || Status.create!(name: name, is_closed: false, is_default: false)
  status_id[name] = s.id
end
puts "ESTADOS_OK ids=#{status_id.values.sort}"

approval_ids   = APPROVAL.map { |n| status_id[n] }
in_review_id   = status_id['En revision']
in_dev_id      = status_id['En desarrollo']
ready_test_id  = status_id['Lista para pruebas']
assigned_id    = status_id['Asignado a la fabrica']
awaiting_id    = status_id['Esperando clarificacion']
appf_id        = status_id['Aprobacion funcional']
vobo_id        = status_id['VoBo del cliente']
appdev_id      = status_id['Aprobacion del desarrollo']
# 'Rejected' es un estado CERRADO por defecto de OpenProject (no se crea aqui).
# Destino terminal para DESCARTAR un requisito (p.ej. duplicado) via el boton
# "Descartar duplicado" del inbox. id nil si la instancia no lo tiene.
rejected_id    = Status.where(name: 'Rejected').first&.id

# --- 2. Tipo Requisito ---
# Nota: los estados quedan asociados al tipo IMPLICITAMENTE al crear
# workflows que los referencien (Workflow{type_id, role_id, old/new_status_id}).
# No hay tabla TypeStatus explicita en este OpenProject; basta con los
# workflows que se crean mas abajo.
req_type = Type.find_by(name: 'Requisito')
abort 'No existe el tipo "Requisito". Corre primero local/create-requisito-type.rb' if req_type.nil?
puts "TIPO_REQUISITO id=#{req_type.id}"

# --- 3. Usuario de servicio ---
user = User.find_by(login: login)
if user
  puts "USUARIO_YA_EXISTE id=#{user.id}"
else
  user = User.new(login: login, firstname: 'Fabro', lastname: 'Agent',
                  mail: 'fabro-agent@epiuse.lat', admin: false, status: :active)
  user.password = password
  user.password_confirmation = password
  user.save!
  puts "USUARIO_CREADO id=#{user.id}"
end

# --- 4. Rol "Agente Fabro" con permisos operativos minimos ---
fabro_perms = %i[
  view_project
  view_work_packages
  add_work_packages
  edit_work_packages
  add_work_package_notes
  add_work_package_comments
  edit_own_work_package_notes
  manage_work_package_relations
  manage_subtasks
  add_work_package_attachments
  view_work_package_watchers
  log_own_time
  view_time_entries
  edit_own_time_entries
]

role = ProjectRole.find_by(name: role_nm)
if role
  to_add = fabro_perms - role.permissions.map(&:to_sym)
  to_add.each { |p| role.add_permission!(p) }
  puts "ROL_YA_EXISTE id=#{role.id} permisos_agregados=#{to_add.size}"
else
  role = ProjectRole.new(name: role_nm)
  role.permissions = fabro_perms
  role.save!
  puts "ROL_CREADO id=#{role.id} permisos=#{fabro_perms.size}"
end

# --- 5. Workflows del rol Fabro para el tipo Requisito: MINIMOS, NUNCA a aprobacion ---
# Re-genera limpiamente para esta corrida.
Workflow.where(role_id: role.id, type_id: req_type.id).destroy_all

# (old, new): solo lo que el orquestador necesita; jamas hacia approval_ids.
fabro_transitions = [
  [Status.where(name: 'New').first&.id, in_review_id], # para pruebas directas
  [assigned_id, in_review_id],                          # entrada via webhook
  [vobo_id,     in_dev_id],                             # tras la compuerta humana
  [in_review_id, in_dev_id],                            # handoff a desarrollo tras los 2 VoBos (gates por senal dejan el WP en 'En revision'); 'En desarrollo' NO es estado de aprobacion, respeta 8.4
  [in_dev_id,   ready_test_id],                         # En desarrollo -> Lista para pruebas (handoff a QA)
  # --- modo blocked del agente de especificacion (brief 9) ---
  # El loop 'diseno' puede bloquear en la iter 1 con el WP aun en 'New'
  # (validado pero sin transicionar): por eso New -> Esperando clarificacion.
  # Sin esta transicion, op_set_status reintenta infinito y cuelga el workflow.
  [Status.where(name: 'New').first&.id, awaiting_id],   # bloqueo en iter 1 (status New)
  [assigned_id, awaiting_id],                           # bloqueo desde el inicio
  [in_review_id, awaiting_id],                          # bloqueo si se re-corre
  [in_dev_id,    awaiting_id],                          # bloqueo en etapas posteriores
  [ready_test_id, awaiting_id],
  [awaiting_id, in_review_id],                          # clarificacion → continuar
  [awaiting_id, Status.where(name: 'New').first&.id],   # clarificacion → volver a New (re-validar)
  # --- descarte: cerrar como Rejected (p.ej. duplicado) desde cualquier punto.
  # Rejected es terminal (is_closed), NO es estado de aprobacion → respeta 8.4.
  [Status.where(name: 'New').first&.id, rejected_id],
  [assigned_id,   rejected_id],
  [in_review_id,  rejected_id],
  [in_dev_id,     rejected_id],
  [ready_test_id, rejected_id],
  [awaiting_id,   rejected_id],
].compact.reject { |o, n| o.nil? || n.nil? }

added = 0
fabro_transitions.each do |old_id, new_id|
  next if approval_ids.include?(new_id)  # defensa en profundidad
  next if Workflow.exists?(type_id: req_type.id, role_id: role.id,
                           old_status_id: old_id, new_status_id: new_id)
  Workflow.create!(type_id: req_type.id, role_id: role.id,
                   old_status_id: old_id, new_status_id: new_id,
                   author: false, assignee: false)
  added += 1
end

# El orquestador maneja TODO el flujo operativo con set_status; si una
# transicion no existe, op_set_status falla 422 y REINTENTA INFINITO -> cuelga
# el workflow (observado en #59 Esperando clarificacion, #40 En revision...).
# Por eso: el agente puede ir a CUALQUIER estatus OPERATIVO desde cualquiera.
# NUNCA a los de APROBACION (approval_ids) — esos los fija un humano (8.4).
op_targets = [assigned_id, in_review_id, in_dev_id, ready_test_id, awaiting_id,
              Status.where(name: 'New').first&.id, rejected_id].compact.uniq
Status.all.each do |s|
  op_targets.each do |tid|
    next if s.id == tid || approval_ids.include?(tid)
    next if Workflow.exists?(type_id: req_type.id, role_id: role.id,
                             old_status_id: s.id, new_status_id: tid)
    Workflow.create!(type_id: req_type.id, role_id: role.id,
                     old_status_id: s.id, new_status_id: tid,
                     author: false, assignee: false)
    added += 1
  end
end
puts "WORKFLOWS_FABRO count=#{added}  approval_destinos_excluidos=#{approval_ids.size}"

# --- 6. Workflows del rol Member para el tipo Requisito: lo que necesitan los humanos ---
member = ProjectRole.find_by(name: 'Member')
if member
  human_transitions = [
    [Status.where(name: 'New').first&.id, assigned_id],  # disparar la fabrica
    [in_review_id, appf_id],                              # consultor funcional aprueba
    [appf_id,      vobo_id],                              # cliente da VoBo
    [in_dev_id,    appdev_id],                            # cliente aprueba desarrollo
    # --- el consultor reanuda tras contestar las preguntas del agente ---
    [awaiting_id,  assigned_id],
  ].compact.reject { |o, _| o.nil? }
  human_added = 0
  human_transitions.each do |old_id, new_id|
    next if Workflow.exists?(role_id: member.id, type_id: req_type.id,
                              old_status_id: old_id, new_status_id: new_id)
    Workflow.create!(type_id: req_type.id, role_id: member.id,
                     old_status_id: old_id, new_status_id: new_id,
                     author: false, assignee: false)
    human_added += 1
  end
  puts "WORKFLOWS_MEMBER_NUEVOS count=#{human_added}"
end

# --- 7. Permiso add_work_package_comments al rol Agente MCP (para que el MCP comente) ---
mcp_role = ProjectRole.find_by(name: 'Agente MCP')
if mcp_role && !mcp_role.permissions.map(&:to_sym).include?(:add_work_package_comments)
  mcp_role.add_permission!(:add_work_package_comments)
  puts 'MCP_ROLE_PERMISO_AGREGADO add_work_package_comments'
end

# --- 8. Membresia de fabro-agent en todos los proyectos ---
added_mem = 0
Project.find_each do |p|
  m = Member.find_or_initialize_by(principal: user, project: p)
  next unless m.new_record?
  m.roles = [role]
  m.save!
  added_mem += 1
end
puts "MEMBRESIAS_NUEVAS count=#{added_mem} total_proyectos=#{Project.count}"

# --- 9. Token de API ---
existing = Token::API.find_by(user_id: user.id)
if existing
  puts 'TOKEN_API_YA_EXISTE (no recuperable). Borralo para regenerar.'
  puts 'TOKEN_API_VALUE='
else
  token = Token::API.create!(user: user)
  puts "TOKEN_API_VALUE=#{token.plain_value}"
end
