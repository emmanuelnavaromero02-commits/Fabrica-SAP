# frozen_string_literal: true
#
# PATCH QUIRÚRGICO: habilita features Enterprise específicas en OpenProject
# Community Edition.
#
# OpenProject CE bloquea ciertas features con
# `EnterpriseToken.allows_to?(:feature)` que devuelve `false` cuando no hay
# token Enterprise activo. Este initializer sobrescribe SOLO las features
# listadas en FABRO_ENABLED_FEATURES para devolver `true`. El resto sigue
# el comportamiento original.
#
# Para agregar/quitar features, editar la constante FABRO_ENABLED_FEATURES.
# Una feature de más NO se habilita sin agregarla aquí explícitamente.
#
# Lista de features Enterprise comunes en OP 17 (no todas las quieres):
#   :sso_auth_providers     SSO con OIDC/SAML (OmniAuth providers)
#   :custom_branding        Logo, colores, nombre de la app
#   :two_factor_authentication  2FA por usuario
#   :baselines              Snapshots de WP en el tiempo
#   :project_status_reports Reportes ejecutivos por proyecto
#   :work_packages_pdf_export
#   :advanced_filtering
#   :advanced_relations
#   :attribute_help_texts   Tooltips de help en custom fields
#   :board_view             Board (Kanban) views
#   :form_configuration     Configuración de forms por tipo
#   :readonly_work_package_attributes
#
# Justificación legal: uso interno (GPL v3 permite modificar). NO se
# distribuye este patch — vive solo en nuestra config local.
#
# Mantenimiento: cada upgrade de OpenProject, verificar que el método
# `EnterpriseToken.allows_to?(feature)` siga existiendo con esa signature.

FABRO_ENABLED_FEATURES = %i[
  sso_auth_providers
  custom_branding
].freeze

Rails.application.config.after_initialize do
  unless EnterpriseToken.singleton_class.instance_variable_get(:@_fabro_patched)
    EnterpriseToken.singleton_class.prepend(Module.new do
      def allows_to?(feature)
        return true if FABRO_ENABLED_FEATURES.include?(feature)
        super
      end
    end)
    EnterpriseToken.singleton_class.instance_variable_set(:@_fabro_patched, true)
    Rails.logger.info("[fabro-patch] Features Enterprise forzadas a true: " +
                      FABRO_ENABLED_FEATURES.inspect)
  end

  # ---------------------------------------------------------------------
  # PATCH 2: omniauth-openid-connect SIEMPRE llama al /userinfo endpoint.
  # En nuestro setup Docker + Keycloak en modo dev, ese call falla con
  # "Access Token Invalid or Expired" porque KC ve el request internamente
  # y el iss del token no matchea su Host header.
  #
  # Solución quirúrgica: sobrescribir UserInfo#user_info para que extraiga
  # los claims directamente del id_token (que ya vino en el token
  # exchange y contiene los mismos campos que userinfo: sub, email,
  # preferred_username, given_name, family_name, etc.).
  # ---------------------------------------------------------------------
  if defined?(OmniAuth::Strategies::OpenIDConnect::UserInfo) &&
     !OmniAuth::Strategies::OpenIDConnect::UserInfo.instance_variable_get(:@_fabro_patched)
    OmniAuth::Strategies::OpenIDConnect::UserInfo.module_eval do
      define_method(:user_info) do
        return @user_info if defined?(@user_info) && @user_info
        # Decodificar id_token (string JWT) sin verificar firma. Es seguro
        # porque el token vino directo del IdP via /token endpoint server-to-
        # server (no a través del user agent).
        raw_id_token = (respond_to?(:id_token) ? id_token : nil) ||
                       (access_token.respond_to?(:id_token) ? access_token.id_token : nil)
        unless raw_id_token.is_a?(String) && raw_id_token.split(".").length == 3
          Rails.logger.warn("[fabro-patch] id_token no disponible o malformado — caigo a userinfo upstream")
          @user_info = access_token.userinfo!
          return @user_info
        end
        payload_b64 = raw_id_token.split(".")[1]
        payload_b64 += "=" * (-payload_b64.length % 4)  # padding base64
        # symbolize_names porque ResponseObject usa attr_accessor (espera
        # symbols); con string keys el `sub` quedaría nil y OP rechaza con
        # "información de autenticación no válida".
        claims = JSON.parse(Base64.urlsafe_decode64(payload_b64),
                            symbolize_names: true)
        @user_info = OpenIDConnect::ResponseObject::UserInfo.new(claims)
      rescue StandardError => e
        Rails.logger.error("[fabro-patch] error decodificando id_token: #{e.class}: #{e.message}")
        @user_info = access_token.userinfo!
      end
    end
    OmniAuth::Strategies::OpenIDConnect::UserInfo.instance_variable_set(:@_fabro_patched, true)
    Rails.logger.info("[fabro-patch] UserInfo#user_info parcheado para leer del id_token")
  end
end
