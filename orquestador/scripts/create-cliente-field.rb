# Fase 0 del rediseño "repo por requerimiento": crea el custom field de
# proyecto "Cliente" en OpenProject, lo habilita en cada proyecto, le pone
# valor (= cliente) y le da al rol del agente permiso para LEERLO por la API.
#
# El cliente determina la ORG de Gitea donde se crea el repo del requerimiento.
#
# Correr con rails runner DENTRO del contenedor web (copiar con docker cp,
# nunca pipe stdin desde PowerShell — se pierde UTF-8). Ver ssm-run-command-ops.
#   docker cp create-cliente-field.rb <web>:/tmp/x.rb
#   docker exec <web> bundle exec rails runner /tmp/x.rb
#
# Idempotente: se puede correr varias veces.
#
# GOTCHAS OP 17 (verificados 2026-06-08, OP 17.4.0):
#  - Project custom fields necesitan: una sección (ProjectCustomFieldSection) +
#    un mapping por proyecto (ProjectCustomFieldProjectMapping). is_for_all se
#    IGNORA para project CFs; mandan los mappings.
#  - El setter project.custom_field_values= filtra por available_custom_fields
#    (que NO incluye el CF aunque haya mapping) → NO persiste. Hay que escribir
#    el CustomValue directo.
#  - La API v3 sólo expone el CF si el rol del token tiene la permission
#    :view_project_attributes (el rol restringido del agente no la trae).

ROLE_NAME = "Agente Fabro"   # rol del usuario cuyo token usa el orquestador

# Cliente por proyecto. Por defecto se DERIVA del nombre del proyecto
# (prefijo "CLIENTE - ..." o "... para CLIENTE"); ajustar el hash de override
# si algún proyecto no encaja en la heurística.
OVERRIDE = {
  # "Nombre exacto del proyecto" => "CLIENTE",
}

def cliente_for(project)
  name = project.name.to_s
  return OVERRIDE[name] if OVERRIDE.key?(name)
  if name.include?(" - ")
    name.split(" - ").first.strip
  elsif name =~ /para\s+(\S+)\s*$/i
    $1.strip
  else
    nil
  end
end

section = ProjectCustomFieldSection.first
raise "No hay ProjectCustomFieldSection (sección de project attributes)" unless section

cf = ProjectCustomField.find_by(name: "Cliente")
if cf.nil?
  cf = ProjectCustomField.new(name: "Cliente")
  cf.field_format = "string"
  cf.is_required = false
  cf.is_for_all = false
  cf.custom_field_section_id = section.id
  cf.save!
  puts "creado ProjectCustomField ##{cf.id} 'Cliente'"
else
  puts "ya existía ProjectCustomField ##{cf.id} 'Cliente'"
end

# Permiso de lectura por API para el rol del agente.
role = Role.find_by(name: ROLE_NAME)
if role.nil?
  puts "AVISO: no existe el rol #{ROLE_NAME.inspect}; saltando permiso"
elsif role.permissions.include?(:view_project_attributes)
  puts "rol #{ROLE_NAME}: ya tenía :view_project_attributes"
else
  role.add_permission!(:view_project_attributes)
  puts "rol #{ROLE_NAME}: agregado :view_project_attributes"
end

# Habilitar + poblar por proyecto.
Project.order(:id).each do |p|
  cli = cliente_for(p)
  if cli.nil? || cli.empty?
    puts "  ##{p.id} #{p.name} -> (sin cliente derivable, skip)"
    next
  end
  ProjectCustomFieldProjectMapping.find_or_create_by!(project_id: p.id, custom_field_id: cf.id)
  cv = CustomValue.find_or_initialize_by(customized_type: "Project", customized_id: p.id, custom_field_id: cf.id)
  cv.value = cli
  cv.save!
  puts "  ##{p.id} #{p.name} -> Cliente=#{cli.inspect}"
end

puts "OK. El orquestador resuelve el CF por NOMBRE ('Cliente') vía /projects/schema, no por customFieldN fijo."
