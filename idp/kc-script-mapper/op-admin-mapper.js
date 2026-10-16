/*
 * Script Protocol Mapper DESPLEGADO (Keycloak Quarkus, feature 'scripts').
 * Emite el claim booleano `op_admin` = true SÓLO si el usuario tiene el rol de
 * realm `admin`. OpenProject lo lee como mapping_admin y lo castea a booleano
 * para conceder/revocar el flag admin en cada login SSO.
 *
 * Se empaqueta como provider JAR (este archivo + META-INF/keycloak-scripts.json)
 * en /opt/keycloak/providers/. El provider resultante se llama
 * `script-op-admin-mapper.js`. Bindings: user, realm, token, userSession,
 * keycloakSession, exports.
 */
var role = realm.getRole("admin");
exports = (role != null) && user.hasRole(role);
