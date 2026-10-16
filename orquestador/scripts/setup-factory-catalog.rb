# Catalogo completo de la fabrica de software (Phase A).
#
# Cierra el gap del modelo enterprise propuesto por el usuario:
#   - Tipos de WP nuevos: Capability, Functional Spec, Prototype, Test Plan,
#     Development Iteration, Backend Task, API Task, UI Task, Technical QA,
#     Functional Testing, UAT, Deployment.
#   - Estados nuevos: In Functional Testing, Functional Testing Approved,
#     In UAT, UAT Approved, UAT Rejected, Ready to Deploy, Deployed,
#     In Hypercare.
#   - Roles nuevos: Consultor Funcional, Arquitecto, Cliente Key User,
#     QA Tecnico, DevOps Basis.
#   - Workflows por (tipo x rol) — Fabro NUNCA hacia estados de aprobacion.
#   - Tipos habilitados en todos los proyectos.
#
# Idempotente.

# === 1. ESTADOS NUEVOS =====================================================
NEW_STATUSES = [
  'In Functional Testing', 'Functional Testing Approved',
  'In UAT', 'UAT Approved', 'UAT Rejected',
  'Ready to Deploy', 'Deployed', 'In Hypercare',
]

status_id = {}
NEW_STATUSES.each do |n|
  s = Status.find_by(name: n) || Status.create!(
    name: n, is_closed: (n == 'Deployed' || n == 'In Hypercare'), is_default: false
  )
  status_id[n] = s.id
end
puts "ESTADOS_NUEVOS count=#{NEW_STATUSES.size}"

# Cache estados existentes que usamos abajo
['New', 'In progress', 'In testing', 'Tested', 'Test failed', 'Closed',
 'Asignado a la fabrica', 'En revision', 'En desarrollo', 'Lista para pruebas',
 'Esperando clarificacion', 'Aprobacion funcional', 'VoBo del cliente',
 'Aprobacion del desarrollo'].each do |n|
  s = Status.find_by(name: n)
  status_id[n] = s&.id
end

# Lista global de estados de aprobacion (Fabro NUNCA transiciona a estos)
APPROVAL_STATUSES = [
  'Aprobacion funcional', 'VoBo del cliente', 'Aprobacion del desarrollo',
  'UAT Approved', 'UAT Rejected', 'Functional Testing Approved',
]
approval_ids = APPROVAL_STATUSES.map { |n| status_id[n] }.compact

# === 2. TIPOS NUEVOS =======================================================
NEW_TYPES = %w[
  Capability
  Functional\ Spec
  Prototype
  Test\ Plan
  Development\ Iteration
  Backend\ Task
  API\ Task
  UI\ Task
  Technical\ QA
  Functional\ Testing
  UAT
  Deployment
]

task_type = Type.find_by(name: 'Task')
abort 'No existe el tipo Task base' if task_type.nil?

type_id = { 'Requisito' => Type.find_by(name: 'Requisito')&.id, 'Task' => task_type.id }
NEW_TYPES.each do |name|
  t = Type.find_by(name: name)
  if t.nil?
    t = Type.new(
      name: name, is_in_roadmap: true, is_milestone: false,
      is_default: false, is_standard: false,
      color_id: task_type.color_id,
      position: (Type.maximum(:position) || 0) + 1,
    )
    t.save!
    puts "TIPO_CREADO id=#{t.id} #{name}"
  else
    puts "TIPO_YA_EXISTE id=#{t.id} #{name}"
  end
  type_id[name] = t.id
end

new_type_ids_only = NEW_TYPES.map { |n| type_id[n] }
all_factory_type_ids = ([type_id['Requisito']] + new_type_ids_only).compact

# === 3. HABILITAR TIPOS NUEVOS EN TODOS LOS PROYECTOS ======================
Project.find_each do |p|
  new_type_ids_only.each do |tid|
    t = Type.find(tid)
    p.types << t unless p.types.include?(t)
  end
end
puts "TIPOS_HABILITADOS_EN_PROYECTOS proyectos=#{Project.count}"

# === 4. WORKFLOWS BASE: copiar Member x Task -> Member x cada tipo nuevo ===
member = ProjectRole.find_by(name: 'Member')
abort 'No existe el rol Member' if member.nil?

new_type_ids_only.each do |tid|
  next if Workflow.exists?(role_id: member.id, type_id: tid)
  copied = 0
  Workflow.where(role_id: member.id, type_id: task_type.id).find_each do |w|
    Workflow.create!(
      role_id: member.id, type_id: tid,
      old_status_id: w.old_status_id, new_status_id: w.new_status_id,
      author: w.author, assignee: w.assignee,
    )
    copied += 1
  end
  puts "WORKFLOWS_MEMBER_BASE #{Type.find(tid).name} copiados=#{copied}"
end

# === 5. WORKFLOWS NUEVOS PARA MEMBER en TODOS los tipos de la fabrica =====
# (Estos cubren el nuevo ciclo Functional Testing -> UAT -> Deployment.)
new_lifecycle = [
  ['In progress', 'In Functional Testing'],
  ['Lista para pruebas', 'In Functional Testing'],
  ['In Functional Testing', 'Functional Testing Approved'],
  ['In Functional Testing', 'Test failed'],
  ['Functional Testing Approved', 'In UAT'],
  ['In UAT', 'UAT Approved'],
  ['In UAT', 'UAT Rejected'],
  ['UAT Approved', 'Ready to Deploy'],
  ['UAT Rejected', 'En desarrollo'],
  ['Ready to Deploy', 'Deployed'],
  ['Deployed', 'In Hypercare'],
  ['In Hypercare', 'Closed'],
]
member_new_count = 0
new_lifecycle.each do |from, to|
  fid, tid = status_id[from], status_id[to]
  next if fid.nil? || tid.nil?
  all_factory_type_ids.each do |type_iid|
    next if Workflow.exists?(role_id: member.id, type_id: type_iid,
                             old_status_id: fid, new_status_id: tid)
    Workflow.create!(
      role_id: member.id, type_id: type_iid,
      old_status_id: fid, new_status_id: tid,
      author: false, assignee: false,
    )
    member_new_count += 1
  end
end
puts "WORKFLOWS_MEMBER_NUEVO_CICLO count=#{member_new_count}"

# === 6. ROLES NUEVOS =======================================================
ROLES = {
  'Consultor Funcional' => %i[
    view_project view_work_packages add_work_packages edit_work_packages
    add_work_package_notes add_work_package_comments edit_own_work_package_notes
    manage_work_package_relations manage_subtasks add_work_package_attachments
    view_work_package_watchers log_own_time view_time_entries edit_own_time_entries
  ],
  'Arquitecto' => %i[
    view_project view_work_packages add_work_packages edit_work_packages
    add_work_package_notes add_work_package_comments edit_own_work_package_notes
    manage_work_package_relations manage_subtasks add_work_package_attachments
    view_work_package_watchers
  ],
  'Cliente Key User' => %i[
    view_project view_work_packages add_work_package_notes
    add_work_package_comments edit_own_work_package_notes
    view_work_package_watchers
  ],
  'QA Tecnico' => %i[
    view_project view_work_packages add_work_packages edit_work_packages
    add_work_package_notes add_work_package_comments edit_own_work_package_notes
    manage_work_package_relations add_work_package_attachments
    view_work_package_watchers
  ],
  'DevOps Basis' => %i[
    view_project view_work_packages edit_work_packages
    add_work_package_notes add_work_package_comments edit_own_work_package_notes
    add_work_package_attachments view_work_package_watchers
  ],
}

role_id = {}
ROLES.each do |name, perms|
  role = ProjectRole.find_by(name: name)
  if role
    to_add = perms - role.permissions.map(&:to_sym)
    to_add.each { |p| role.add_permission!(p) }
    puts "ROL_YA_EXISTE #{name} permisos_agregados=#{to_add.size}"
  else
    role = ProjectRole.new(name: name)
    role.permissions = perms
    role.save!
    puts "ROL_CREADO #{name} permisos=#{perms.size}"
  end
  role_id[name] = role.id
end

# === 7. WORKFLOWS POR ROL ESPECIFICO =======================================

# 7.1 Consultor Funcional y Arquitecto: full Member-like en todos los tipos
['Consultor Funcional', 'Arquitecto'].each do |role_name|
  rid = role_id[role_name]
  copied = 0
  all_factory_type_ids.each do |tid|
    Workflow.where(role_id: member.id, type_id: tid).find_each do |w|
      next if Workflow.exists?(role_id: rid, type_id: tid,
                               old_status_id: w.old_status_id, new_status_id: w.new_status_id)
      Workflow.create!(
        role_id: rid, type_id: tid,
        old_status_id: w.old_status_id, new_status_id: w.new_status_id,
        author: w.author, assignee: w.assignee,
      )
      copied += 1
    end
  end
  puts "WORKFLOWS_#{role_name.tr(' ', '_').upcase} count=#{copied}"
end

# 7.2 Cliente Key User: SOLO transiciones de UAT
client_uat_tid = type_id['UAT']
client_count = 0
[
  ['In UAT', 'UAT Approved'],
  ['In UAT', 'UAT Rejected'],
].each do |from, to|
  fid, tid = status_id[from], status_id[to]
  next if fid.nil? || tid.nil? || client_uat_tid.nil?
  next if Workflow.exists?(role_id: role_id['Cliente Key User'], type_id: client_uat_tid,
                           old_status_id: fid, new_status_id: tid)
  Workflow.create!(
    role_id: role_id['Cliente Key User'], type_id: client_uat_tid,
    old_status_id: fid, new_status_id: tid,
    author: false, assignee: false,
  )
  client_count += 1
end
# Cliente tambien aprueba el VoBo del Requirement (decision macro)
[['En revision', 'VoBo del cliente'],
 ['En desarrollo', 'Aprobacion del desarrollo']].each do |from, to|
  fid, tid = status_id[from], status_id[to]
  next if fid.nil? || tid.nil?
  next if Workflow.exists?(role_id: role_id['Cliente Key User'], type_id: type_id['Requisito'],
                           old_status_id: fid, new_status_id: tid)
  Workflow.create!(
    role_id: role_id['Cliente Key User'], type_id: type_id['Requisito'],
    old_status_id: fid, new_status_id: tid, author: false, assignee: false,
  )
  client_count += 1
end
puts "WORKFLOWS_CLIENTE_KEY_USER count=#{client_count}"

# 7.3 QA Tecnico: transiciones de Technical QA + Functional Testing
qa_types = [type_id['Technical QA'], type_id['Functional Testing']].compact
qa_count = 0
[
  ['New', 'In progress'],
  ['In progress', 'In testing'],
  ['In testing', 'Tested'],
  ['In testing', 'Test failed'],
  ['Lista para pruebas', 'In Functional Testing'],
  ['In Functional Testing', 'Functional Testing Approved'],
  ['In Functional Testing', 'Test failed'],
  ['Test failed', 'En desarrollo'],
].each do |from, to|
  fid, tid = status_id[from], status_id[to]
  next if fid.nil? || tid.nil?
  qa_types.each do |type_iid|
    next if Workflow.exists?(role_id: role_id['QA Tecnico'], type_id: type_iid,
                             old_status_id: fid, new_status_id: tid)
    Workflow.create!(
      role_id: role_id['QA Tecnico'], type_id: type_iid,
      old_status_id: fid, new_status_id: tid, author: false, assignee: false,
    )
    qa_count += 1
  end
end
puts "WORKFLOWS_QA_TECNICO count=#{qa_count}"

# 7.4 DevOps Basis: transiciones de Deployment
deploy_type = type_id['Deployment']
devops_count = 0
[
  ['UAT Approved', 'Ready to Deploy'],
  ['Ready to Deploy', 'Deployed'],
  ['Deployed', 'In Hypercare'],
  ['In Hypercare', 'Closed'],
].each do |from, to|
  fid, tid = status_id[from], status_id[to]
  next if fid.nil? || tid.nil? || deploy_type.nil?
  next if Workflow.exists?(role_id: role_id['DevOps Basis'], type_id: deploy_type,
                           old_status_id: fid, new_status_id: tid)
  Workflow.create!(
    role_id: role_id['DevOps Basis'], type_id: deploy_type,
    old_status_id: fid, new_status_id: tid, author: false, assignee: false,
  )
  devops_count += 1
end
puts "WORKFLOWS_DEVOPS_BASIS count=#{devops_count}"

# === 8. EXTENDER FABRO PARA LOS TIPOS NUEVOS ===============================
# Brief 8.4: Fabro NUNCA hacia estados de aprobacion.
fabro = ProjectRole.find_by(name: 'Agente Fabro')
if fabro
  fabro_count = 0
  # Por cada tipo nuevo, copia los workflows de Member EXCLUYENDO
  # destinos que sean estados de aprobacion.
  new_type_ids_only.each do |tid|
    Workflow.where(role_id: member.id, type_id: tid).find_each do |w|
      next if approval_ids.include?(w.new_status_id)
      next if Workflow.exists?(role_id: fabro.id, type_id: tid,
                               old_status_id: w.old_status_id, new_status_id: w.new_status_id)
      Workflow.create!(
        role_id: fabro.id, type_id: tid,
        old_status_id: w.old_status_id, new_status_id: w.new_status_id,
        author: w.author, assignee: w.assignee,
      )
      fabro_count += 1
    end
  end
  puts "WORKFLOWS_FABRO_TIPOS_NUEVOS count=#{fabro_count}"
end

puts 'CATALOGO_OK'
