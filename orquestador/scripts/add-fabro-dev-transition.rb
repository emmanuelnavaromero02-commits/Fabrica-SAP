# Aditivo idempotente: concede al rol "Agente Fabro" la transicion
# 'En revision' -> 'En desarrollo' para el tipo Requisito.
#
# Por que: las compuertas de VoBo del orquestador se aprueban por SENAL
# (no cambian el estado en OP), asi que el WP queda en 'En revision' tras los
# dos VoBos. El handoff a desarrollo lo hace el agente (fabro) y necesita esta
# transicion. 'En desarrollo' NO es estado de aprobacion -> respeta la
# frontera 8.4 (el agente no aprueba; solo mueve el handoff tras la
# aprobacion humana). La transicion 'En desarrollo' -> 'Lista para pruebas'
# ya existia.
#
# Tambien esta incluido en create-fabro-agent.rb; este script lo aplica sin
# re-ejecutar todo el setup. Ejecutar con: rails runner add-fabro-dev-transition.rb

role  = ProjectRole.find_by(name: 'Agente Fabro')
req   = Type.find_by(name: 'Requisito')
inrev = Status.find_by(name: 'En revision')
indev = Status.find_by(name: 'En desarrollo')

if role && req && inrev && indev
  if Workflow.exists?(role_id: role.id, type_id: req.id,
                      old_status_id: inrev.id, new_status_id: indev.id)
    puts 'TRANSITION_ALREADY_EXISTS En revision -> En desarrollo (Agente Fabro)'
  else
    Workflow.create!(type_id: req.id, role_id: role.id,
                     old_status_id: inrev.id, new_status_id: indev.id,
                     author: false, assignee: false)
    puts 'TRANSITION_ADDED En revision -> En desarrollo (Agente Fabro)'
  end
else
  puts "MISSING role=#{role&.id} req=#{req&.id} inrev=#{inrev&.id} indev=#{indev&.id}"
end
