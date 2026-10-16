# Provisiona en OpenProject SOLO a los PM (swf-project-manager / project-manager).
# El resto de roles NO va a OP (operan por el Inbox).
#
# Mecanismo verificado: OP enlaza la identidad OIDC por
#   users.identity_url = "keycloak:<sub>"
# y auto-crea la cuenta en el primer login SSO (self_registration=3). Pero un
# usuario sin membresía no ve nada. Este script:
#   1. pre-crea/enlaza el usuario OP (identity_url = keycloak:<sub>),
#   2. le da membresía con rol PM en los proyectos.
# Idempotente: re-ejecutarlo no duplica.
#
# Entrada: /tmp/pm-users.json = [{login, sub, mail, firstname, lastname}]
#          (lo arma el caller desde la Keycloak Admin API).
# Env: PM_OP_ROLE (rol de proyecto, default 'Project admin').
#
# Pre-link OIDC: con identity_url seteado, el primer login SSO del PM mapea a
# esta cuenta (no crea una nueva). Ver [[gestion-usuarios-sso.md]].

require 'json'

path = '/tmp/pm-users.json'
abort "FALTA #{path}" unless File.exist?(path)
pms = JSON.parse(File.read(path))

role_name = ENV['PM_OP_ROLE'].to_s.strip
role_name = 'Project admin' if role_name.empty?
role = Role.find_by(name: role_name) || Role.find_by(name: 'Member')
abort "No existe rol de proyecto '#{role_name}' ni 'Member'" if role.nil?
puts "ROL_PROYECTO=#{role.name} (id=#{role.id})"

projects = Project.active.to_a
puts "PROYECTOS=#{projects.size}"

# OP auto-crea y ENLAZA la cuenta (identity_url=keycloak:<sub>) en el primer
# login SSO (self_registration=3). identity_url NO es asignable por Rails en
# OP17, así que NO pre-creamos: emparejamos por login (=preferred_username) o
# mail con la cuenta auto-creada y le agregamos membresía + rol. Si el PM aún
# no entró por SSO, queda PENDIENTE (re-correr tras su primer login).
provisioned = 0
pending = 0
pms.each do |pm|
  login = pm['login'].to_s
  mail  = pm['mail'].to_s
  u = (User.find_by(login: login) if login.present?)
  u ||= (User.find_by(mail: mail) if mail.present?)
  if u.nil?
    pending += 1
    puts "PENDIENTE #{login}: aún sin cuenta en OP (hará login SSO → se " \
         "auto-crea; re-correr provisioning para darle membresía)"
    next
  end
  added = 0
  projects.each do |p|
    m = Member.find_or_initialize_by(principal: u, project: p)
    next unless m.new_record?
    m.roles = [role]
    m.save!
    added += 1
  end
  provisioned += 1
  puts "OK #{u.login} id=#{u.id} membresias_nuevas=#{added} (de #{projects.size})"
end
puts "PROVISION_OK provisionados=#{provisioned} pendientes=#{pending} de #{pms.size}"
