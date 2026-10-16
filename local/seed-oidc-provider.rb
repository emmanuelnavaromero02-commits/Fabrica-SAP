# Crea/actualiza el proveedor OpenID Connect "Keycloak" en OpenProject.
# IDEMPOTENTE (find_or_initialize por slug). Reproducible: reemplaza el paso
# manual del Admin UI (Administración → Autenticación → Proveedores OpenID) que
# no quedaba versionado y por eso no se replicaba en AWS.
#
# OP 17.4: el proveedor es un OpenIDConnect::Provider (STI sobre auth_providers);
# la config vive en la columna JSON `options` (issuer/metadata_url, client_id,
# client_secret, scope, mapping_*). El slug genera el callback
# /auth/oidc-<slug>/callback — debe coincidir con el redirect URI del client del
# realm (oidc-keycloak).
#
# Ejecutar dentro del contenedor OP 'web' pasando la config por ENV:
#   docker exec \
#     -e OIDC_METADATA_URL="https://kc.<dominio>/realms/fabrica/.well-known/openid-configuration" \
#     -e OIDC_CLIENT_ID="openproject" \
#     -e OIDC_CLIENT_SECRET="<secret del client del realm>" \
#     <contenedor_web> bundle exec rails runner /tmp/seed-oidc-provider.rb
#
# Variables (con defaults sensatos salvo las requeridas):
#   OIDC_METADATA_URL   (requerida) discovery del realm
#   OIDC_CLIENT_SECRET  (requerida) secret del client 'openproject' en Keycloak
#   OIDC_CLIENT_ID      (default 'openproject')
#   OIDC_SLUG           (default 'keycloak')  -> callback /auth/oidc-keycloak/callback
#   OIDC_DISPLAY_NAME   (default 'Keycloak')  -> texto del botón de login
#   OIDC_SCOPE          (default 'openid email profile')

slug     = ENV.fetch("OIDC_SLUG", "keycloak")
display  = ENV.fetch("OIDC_DISPLAY_NAME", "Keycloak")
metadata = ENV.fetch("OIDC_METADATA_URL")
clientid = ENV.fetch("OIDC_CLIENT_ID", "openproject")
secret   = ENV.fetch("OIDC_CLIENT_SECRET")
# OJO: la columna `scope` de OP es STRING — si le pasas un Array le hace .to_s y
# guarda "[:openid, :email, :profile]" (con corchetes), que Keycloak rechaza con
# invalid_scope. Debe ser un STRING separado por espacios: "openid email profile".
scope    = (ENV["OIDC_SCOPE"] || "openid email profile").to_s.strip

p = OpenIDConnect::Provider.find_or_initialize_by(slug: slug)
nuevo = p.new_record?

p.display_name = display
p.available = true if p.respond_to?(:available=)
# creator es NOT NULL en algunas versiones: usa el primer admin disponible.
if p.respond_to?(:creator=) && p.try(:creator_id).nil?
  admin = User.where(admin: true).order(:id).first
  p.creator = admin if admin
end

p.oidc_provider = "custom" if p.respond_to?(:oidc_provider=)
p.metadata_url  = metadata
p.client_id     = clientid
p.client_secret = secret
p.scope         = scope

# OP no descarga el discovery al guardar el modelo directo (eso lo hace el
# servicio del Admin UI). Sin endpoints poblados, metadata_configured? = false
# y el login NO muestra el botón. Los traemos del .well-known y los fijamos.
begin
  require "net/http"; require "json"; require "uri"
  u = URI(metadata)
  http = Net::HTTP.new(u.host, u.port)
  http.use_ssl = (u.scheme == "https")
  http.open_timeout = 10; http.read_timeout = 10
  meta = JSON.parse(http.get(u.request_uri).body)
  p.issuer                 = meta["issuer"]
  p.authorization_endpoint = meta["authorization_endpoint"]
  p.token_endpoint         = meta["token_endpoint"]
  p.userinfo_endpoint      = meta["userinfo_endpoint"]
  p.jwks_uri               = meta["jwks_uri"]
  p.end_session_endpoint   = meta["end_session_endpoint"]
  puts "discovery OK issuer=#{p.issuer}"
rescue => e
  puts "WARN discovery falló (#{e.class}: #{e.message}) — el provider quedará incompleto"
end
# Mapeo de claims estándar de Keycloak -> atributos de usuario de OP.
p.mapping_login      = "preferred_username"
p.mapping_email      = "email"
p.mapping_first_name = "given_name"
p.mapping_last_name  = "family_name"
# mapping_admin (opcional): nombre del claim BOOLEANO que concede admin en OP
# (omniauth_service castea con ActiveRecord::Type::Boolean). Lo emite un Script
# Mapper en Keycloak (op_admin=true sólo si el usuario tiene el rol 'admin').
# Si se deja vacío, OP NUNCA toca el flag admin (attribute_map lo descarta).
p.mapping_admin = ENV["OIDC_MAPPING_ADMIN"] if ENV["OIDC_MAPPING_ADMIN"].to_s.strip != ""

p.save!
puts((nuevo ? "CREADO" : "ACTUALIZADO") +
     " id=#{p.id} slug=#{p.slug} display=#{p.display_name} available=#{p.try(:available)}")
puts "metadata_configured?=#{p.metadata_configured? rescue 'n/a'} mapping_configured?=#{p.mapping_configured? rescue 'n/a'}"
puts "callback esperado: /auth/oidc-#{p.slug}/callback"
puts "--- Proveedores OIDC actuales ---"
OpenIDConnect::Provider.all.each do |x|
  puts "  ##{x.id} #{x.slug} (#{x.display_name}) available=#{x.try(:available)}"
end
