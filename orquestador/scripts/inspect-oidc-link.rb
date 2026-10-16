# Diagnostico: como enlaza OpenProject la identidad OIDC (para pre-link).
puts "=== users (login / identity_url / mail) ==="
User.where.not(type: 'SystemUser').order(:id).limit(30).each do |u|
  puts "#{u.id}\t#{u.login}\t#{u.identity_url.inspect}\t#{u.mail}"
end
puts "=== auth settings ==="
begin
  puts "self_registration=#{Setting.self_registration.inspect}"
  puts "oidc plugin=#{Setting.plugin_openproject_openid_connect.inspect}" rescue puts "no oidc plugin setting"
rescue => e
  puts "err #{e}"
end
puts "=== omniauth providers ==="
begin
  puts (OpenProject::Plugins::AuthPlugin.providers.map { |p| p[:name] || p['name'] } rescue 'n/a').inspect
rescue => e
  puts "err providers: #{e}"
end
