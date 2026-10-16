# Crea el tipo de work package "Requisito" en OpenProject. Idempotente.
# Se ejecuta dentro del contenedor 'web' con: bundle exec rails runner

type_name = 'Requisito'

existing = Type.find_by(name: type_name)
if existing
  puts "YA_EXISTE id=#{existing.id} name=#{existing.name} workflows=#{existing.workflows.count}"
else
  # Tipo del que copiar el workflow para que 'Requisito' funcione desde ya.
  source = Type.find_by(name: 'Task') ||
           Type.where(is_standard: true).first ||
           Type.where(is_milestone: false).order(:position).first ||
           Type.first

  t = Type.new(
    name:          type_name,
    is_in_roadmap: true,
    is_milestone:  false,
    is_default:    false,
    is_standard:   false
  )
  t.color    = source&.color
  t.position = (Type.maximum(:position) || 0) + 1
  t.save!
  puts "CREADO id=#{t.id} name=#{t.name}"

  copied = 0
  if source
    source.workflows.find_each do |w|
      Workflow.create!(
        type_id:       t.id,
        role_id:       w.role_id,
        old_status_id: w.old_status_id,
        new_status_id: w.new_status_id,
        author:        w.author,
        assignee:      w.assignee
      )
      copied += 1
    end
  end
  puts "WORKFLOWS_COPIADOS desde=#{source&.name} count=#{copied}"
end

puts '--- Tipos de work package ---'
Type.order(:position).each { |x| puts "  ##{x.id} #{x.name}#{x.is_standard ? ' (standard)' : ''}" }
puts '--- Proyectos (donde puedes habilitar el tipo) ---'
Project.order(:id).each { |p| puts "  ##{p.id} #{p.name} [#{p.identifier}]" }
