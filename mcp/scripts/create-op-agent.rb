# Crea el usuario de servicio del componente MCP en OpenProject:
#   - usuario 'mcp-agent' (no administrador),
#   - rol de proyecto "Agente MCP" con permisos minimos,
#   - membresia de ese usuario en todos los proyectos existentes,
#   - un token de API para el usuario.
#
# Idempotente: re-ejecutarlo no duplica nada. Mismo patron que
# local/create-requisito-type.rb.
# Se ejecuta dentro del contenedor 'web' con: bundle exec rails runner -
#
# La contrasena se toma de la variable de entorno MCP_AGENT_PASSWORD.

login    = 'mcp-agent'
role_nm  = 'Agente MCP'
password = ENV['MCP_AGENT_PASSWORD'].to_s
abort 'FALTA MCP_AGENT_PASSWORD' if password.empty?

# --- Usuario de servicio ---------------------------------------------------
user = User.find_by(login: login)
if user
  puts "USUARIO_YA_EXISTE id=#{user.id} login=#{user.login}"
else
  user = User.new(
    login:     login,
    firstname: 'MCP',
    lastname:  'Agent',
    mail:      'mcp-agent@epiuse.lat',
    admin:     false,
    status:    :active
  )
  user.password = password
  user.password_confirmation = password
  user.save!
  puts "USUARIO_CREADO id=#{user.id} login=#{user.login}"
end

# --- Rol de proyecto con permisos minimos ----------------------------------
# Sin permisos de administracion de proyecto (no borra work packages, no
# gestiona miembros ni versiones, no edita la configuracion del proyecto).
perms = %i[
  view_project
  view_work_packages
  add_work_packages
  edit_work_packages
  add_work_package_notes
  edit_own_work_package_notes
  manage_work_package_relations
  add_work_package_attachments
  view_work_package_watchers
  log_own_time
  view_time_entries
  edit_own_time_entries
]

role = ProjectRole.find_by(name: role_nm)
if role
  puts "ROL_YA_EXISTE id=#{role.id} name=#{role.name}"
else
  role = ProjectRole.new(name: role_nm)
  role.permissions = perms
  role.save!
  puts "ROL_CREADO id=#{role.id} name=#{role.name} permisos=#{perms.size}"
end

# Workflows: copia las transiciones de un rol existente para que el agente
# pueda mover work packages entre estados de forma normal. Si quieres acotar
# las transiciones del agente, edita los workflows del rol "Agente MCP" en
# Administracion -> Work packages -> Estados.
src_role = ProjectRole.where(builtin: 0).where.not(id: role.id)
                      .order(:id).first
copied = 0
if src_role && role.workflows.empty?
  Workflow.where(role_id: src_role.id).find_each do |w|
    Workflow.create!(
      type_id:       w.type_id,
      role_id:       role.id,
      old_status_id: w.old_status_id,
      new_status_id: w.new_status_id,
      author:        w.author,
      assignee:      w.assignee
    )
    copied += 1
  end
end
puts "WORKFLOWS_COPIADOS desde=#{src_role&.name.inspect} count=#{copied}"

# --- Membresia en todos los proyectos existentes ---------------------------
added = 0
Project.find_each do |p|
  m = Member.find_or_initialize_by(principal: user, project: p)
  next unless m.new_record?
  m.roles = [role]
  m.save!
  added += 1
end
puts "MEMBRESIAS_NUEVAS count=#{added} total_proyectos=#{Project.count}"

# --- Token de API ----------------------------------------------------------
# El valor en claro solo es visible al crearlo. Si ya existe uno, este script
# no lo puede recuperar: borra el token viejo y re-ejecuta para regenerarlo.
existing = Token::API.find_by(user_id: user.id)
if existing
  puts 'TOKEN_API_YA_EXISTE (valor no recuperable). Para regenerarlo:'
  puts "  Token::API.where(user_id: #{user.id}).destroy_all  y re-ejecuta."
  puts 'TOKEN_API_VALUE='
else
  token = Token::API.create!(user: user)
  puts "TOKEN_API_VALUE=#{token.plain_value}"
end
