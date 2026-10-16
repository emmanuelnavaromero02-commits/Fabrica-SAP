"""
SAP Tools for Enterprise MCP Server
All tools use principal propagation authentication when enabled
"""

import os
import sys
import time
import logging
from typing import Dict, Any, Tuple
from functools import wraps

logger = logging.getLogger(__name__)


# ==========================================================================
# Guard de ambiente QAS  (endurecimiento EN EL MCP, no a criterio del LLM)
# ==========================================================================
# Convención: si el system_id contiene "qas" (case-insensitive) el sistema es
# de VALIDACIÓN DE ESCENARIOS y por tanto de SOLO LECTURA. Toda tool que NO esté
# en el allowlist de solo-lectura se aborta ANTES de tocar SAP.
#
# Diseño fail-safe (default-deny): lo que no esté listado aquí queda BLOQUEADO en
# QAS. Una tool nueva se bloquea por default hasta clasificarla en este set.
# Correcciones y desarrollo van SIEMPRE en el ambiente DEV del cliente.
QAS_READONLY_TOOLS = frozenset({
    "aws_abap_cb_connection_status",
    "aws_abap_cb_get_objects",
    "aws_abap_cb_get_source",
    "aws_abap_cb_search_object",
    "aws_abap_cb_check_syntax",
    "aws_abap_cb_get_service_binding_status",
    "aws_abap_cb_run_atc_check",
    "aws_abap_cb_get_test_classes",
    "aws_abap_cb_get_migration_analysis",
    "aws_abap_cb_get_transport_requests",
    "aws_abap_cb_list_transport_objects",
    "aws_abapgit_leer_tabla",
    "aws_abapgit_serializar",
    "aws_abapgit_serializar_lote",
    "aws_abapgit_serializar_estado",
    "aws_abapgit_serializar_resultado",
})


class QASWriteBlocked(PermissionError):
    """Se intentó una operación no-lectura contra un sistema QAS (ambiente de validación)."""


def _system_is_qas(system_id: str) -> bool:
    """La convención: 'qas' como parte del nombre del system_id => ambiente QAS."""
    return bool(system_id) and "qas" in system_id.lower()


def _resolve_tool_name() -> str:
    """Nombre de la tool que invocó a _get_auth_context, leído del call-stack.

    No depende de que la tool lo pase como argumento: así el enforcement no queda
    a criterio del LLM ni de recordar taggear cada tool. Todas las tools se llaman
    aws_abap* y llaman a _get_auth_context directamente en su cuerpo.
    """
    try:
        frame = sys._getframe(2)  # 0=este helper, 1=_get_auth_context, 2=la tool
    except (ValueError, AttributeError):
        return None
    depth = 0
    while frame is not None and depth < 8:
        name = frame.f_code.co_name
        if name.startswith("aws_abap"):
            return name
        frame = frame.f_back
        depth += 1
    return None


def _enforce_qas_guard(system_id: str, tool_name: str) -> None:
    """Aborta la tool si el sistema es QAS y la tool no es de solo-lectura."""
    if not _system_is_qas(system_id):
        return
    if tool_name in QAS_READONLY_TOOLS:
        logger.info(f"QAS-GUARD: '{tool_name}' permitida (solo-lectura) en sistema QAS '{system_id}'")
        return
    logger.warning(f"QAS-GUARD: BLOQUEADA '{tool_name or 'desconocida'}' en sistema QAS '{system_id}'")
    raise QASWriteBlocked(
        f"🚫 Operación bloqueada por política de ambiente. El sistema '{system_id}' es QAS "
        f"(solo VALIDACIÓN DE ESCENARIOS = solo lectura). La herramienta "
        f"'{tool_name or 'desconocida'}' modifica SAP o no está clasificada como solo-lectura, "
        f"por lo que se aborta. Las correcciones y el desarrollo se hacen SIEMPRE en el ambiente DEV."
    )


def register_sap_tools(mcp, server):
    """
    Register all SAP tools with principal propagation support.
    
    Args:
        mcp: FastMCP instance
        server: EnterpriseABAPAcceleratorServer instance
    """
    from enterprise.usage_tracker import enterprise_usage_tracker
    
    def _extract_user_identity(headers: Dict[str, str]) -> Tuple[str, str]:
        """
        Extract user identity from request headers using IAM Identity Validator.
        
        Returns:
            Tuple of (user_id, login_identifier)
            - user_id: Username for display/logging
            - login_identifier: Pass-through value for certificate CN (what user typed to login)
        """
        from auth.iam_identity_validator import IAMIdentityValidator
        from server.oauth_helpers import check_authentication_and_challenge, MCPAuthenticationRequired
        from server.fastmcp_oauth_integration import get_user_from_request
        
        # Log ALL headers for debugging Amazon Q Developer
        logger.info(f"AUTH: All request headers: {dict(headers)}")
        
        # Try FastMCP OAuth first (NEW approach)
        try:
            user_from_oauth = get_user_from_request()
            if user_from_oauth:
                logger.info(f"AUTH: User from FastMCP OAuth: {user_from_oauth}")
                # For OAuth, the user_from_oauth is the login identifier (pass-through)
                return user_from_oauth, user_from_oauth
        except Exception as e:
            logger.debug(f"AUTH: FastMCP OAuth not available: {e}")
        
        # Fallback to IAM Identity Validator (existing approach)
        validator = IAMIdentityValidator()
        identity_info = validator.extract_identity_from_headers(headers)
        
        if identity_info:
            # Get login_identifier for pass-through CN (what user typed to login)
            login_identifier = identity_info.get('login_identifier')
            user_email = identity_info.get('email')
            
            # Extract username part (before @) for display, but keep login_identifier for CN
            if user_email:
                if '@' in user_email:
                    username = user_email.split('@')[0]
                    logger.info(f"AUTH: Extracted user identity: {username}, login_identifier: {login_identifier} (source: {identity_info.get('source')})")
                    return username, login_identifier or user_email
                else:
                    logger.info(f"AUTH: Extracted user identity: {user_email}, login_identifier: {login_identifier} (source: {identity_info.get('source')})")
                    return user_email, login_identifier or user_email
        
        # Fallback: If no identity found, check if OAuth challenge should be returned
        logger.warning(f"AUTH: No user identity found in headers. Available headers: {list(headers.keys())}")
        
        # Check if legacy OAuth challenge should be returned (feature-flagged)
        auth_challenge = check_authentication_and_challenge('anonymous')
        if auth_challenge:
            raise MCPAuthenticationRequired(
                "Authentication required. Please authenticate to access SAP tools.",
                auth_challenge
            )
        
        return None, None
    
    async def _get_auth_context(sap_system_id: str = None) -> Tuple[Any, Dict[str, Any], Dict[str, str]]:
        """
        Get SAP client and context with hybrid approach.
        
        Priority order for SAP system ID:
        1. Tool parameter (sap_system_id argument)
        2. HTTP header (x-sap-system-id)
        3. Environment variable (DEFAULT_SAP_SYSTEM_ID)
        
        Args:
            sap_system_id: Optional SAP system ID from tool parameter
            
        Returns:
            Tuple of (sap_client, context, headers)
        """
        from fastmcp.server.dependencies import get_http_headers
        
        try:
            headers = get_http_headers()
        except:
            headers = {}
        
        user_id, login_identifier = _extract_user_identity(headers)
        
        # Hybrid approach: Priority order for system_id
        # 1. Tool parameter (highest priority)
        # 2. HTTP header
        # 3. Environment variable (lowest priority)
        system_id = None
        source = None
        
        if sap_system_id:
            system_id = sap_system_id
            source = "tool parameter"
        elif headers.get('x-sap-system-id'):
            system_id = headers.get('x-sap-system-id')
            source = "HTTP header"
        else:
            system_id = os.getenv('DEFAULT_SAP_SYSTEM_ID', 'default-sap-system')
            source = "environment variable"
        
        logger.info(f"AUTH: Using SAP system ID '{system_id}' from {source}")

        # Guard de ambiente: QAS = solo lectura. Aborta ANTES de conectar/tocar SAP.
        # El nombre de la tool se lee del call-stack (no se delega al LLM).
        _enforce_qas_guard(system_id, _resolve_tool_name())

        # Fallback for user_id if principal propagation is enabled
        if server.principal_propagation_enabled and not user_id:
            user_id = os.getenv('DEFAULT_USER_ID', 'service-account')
            login_identifier = user_id  # Use same as user_id for fallback
            logger.info(f"AUTH: No user identity in headers, using default: {user_id}")
        
        sap_client, context = await server._get_sap_client_and_context(user_id, system_id, login_identifier)
        return sap_client, context, headers
    
    def _format_context_info(context: Dict[str, Any], extra_info: str = "") -> str:
        """Format context info for tool response."""
        auth_mode = context.get('auth_mode', 'unknown')
        info = f"Authentication: {auth_mode.upper()}\n"
        info += f"SAP User: {context.get('sap_username')}@{context.get('sap_host')}\n"
        if extra_info:
            info += extra_info
        info += "=" * 50 + "\n\n"
        return info
    
    def _track_usage(tool_name: str, context: Dict, headers: Dict, start_time: float, success: bool, error_message: str = None):
        """Track tool usage for analytics."""
        try:
            duration_ms = int((time.time() - start_time) * 1000)
            enterprise_usage_tracker.track_tool_usage(
                user_id=context.get('iam_identity', 'unknown'),
                system_id=headers.get('x-sap-system-id', 'unknown'),
                session_id=f"session_{int(time.time())}",
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=success,
                error_message=error_message,
                team_id=headers.get('x-team-id', 'unknown'),
                request_id=f"req_{int(time.time())}"
            )
        except Exception as e:
            logger.error(f"Error tracking usage: {e}")

    # ==================== SAP TOOLS ====================
    
    @mcp.tool()
    async def aws_abap_cb_connection_status(sap_system_id: str = None) -> str:
        """
        Check SAP connection status
        
        Args:
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = tool_handlers.handle_connection_status(True)
            
            return _format_context_info(context) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error in connection status: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_connection_status", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_get_objects(package_name: str = None, sap_system_id: str = None) -> str:
        """
        Get ABAP objects from SAP system
        
        Args:
            package_name: Optional package name to filter objects
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_objects(package_name)
            
            extra = f"Package: {package_name or 'All packages'}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting objects: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_objects", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_get_source(object_name: str, object_type: str, explanation: str = None, sap_system_id: str = None) -> str:
        """
        Get source code of ABAP object
        
        Args:
            object_name: Name of the ABAP object
            object_type: Type of object (e.g., 'CLAS', 'INTF', 'PROG')
            explanation: Optional explanation for the request
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_source(object_name, object_type)
            
            extra = f"Object: {object_name} ({object_type})\n"
            return _format_context_info(context, extra) + str(result)
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting source: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_source", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_search_object(query: str, object_type: str = None, package_name: str = None, max_results: int = 50, include_inactive: bool = False, sap_system_id: str = None) -> str:
        """
        Search for ABAP objects
        
        Args:
            query: Search query string
            object_type: Optional object type filter
            package_name: Optional package name filter
            max_results: Maximum number of results (default: 50)
            include_inactive: Include inactive objects (default: False)
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_search_object({
                'query': query,
                'object_type': object_type,
                'package_name': package_name,
                'max_results': max_results,
                'include_inactive': include_inactive
            })
            
            extra = f"Search: {query}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error searching: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_search_object", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_create_object(name: str, type: str, description: str, package_name: str = None, source_code: str = None, service_definition: str = None, binding_type: str = None, behavior_definition: str = None, is_test_class: bool = False, interfaces: list = None, super_class: str = None, visibility: str = "PUBLIC", methods: list = None, transport_request: str = None, sap_system_id: str = None) -> str:
        """
        Create new ABAP object
        
        Args:
            name: Name of the object to create
            type: Type of object (e.g., 'CLAS', 'INTF', 'PROG')
            description: Description of the object
            package_name: Package name (default: $TMP)
            source_code: Optional source code
            service_definition: Optional service definition
            binding_type: Optional binding type
            behavior_definition: Optional behavior definition
            is_test_class: Whether this is a test class
            interfaces: List of interfaces to implement
            super_class: Super class name
            visibility: Visibility (PUBLIC, PROTECTED, PRIVATE)
            methods: List of methods
            transport_request: Transport request number
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_create_object({
                'name': name, 'type': type, 'description': description,
                'package_name': package_name or "$TMP", 'source_code': source_code,
                'service_definition': service_definition, 'binding_type': binding_type,
                'behavior_definition': behavior_definition, 'is_test_class': is_test_class,
                'interfaces': interfaces, 'super_class': super_class,
                'visibility': visibility, 'methods': methods, 'transport_request': transport_request
            })
            
            extra = f"Created: {name} ({type})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error creating object: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_create_object", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_update_source(object_name: str, object_type: str, source_code: str = None, methods: list = None, add_interface: str = None, transport_request: str = None, sap_system_id: str = None) -> str:
        """
        Update source code of ABAP object

        Args:
            object_name: Name of the object to update
            object_type: Type of object
            source_code: New source code
            methods: List of methods to update
            add_interface: Interface to add
            transport_request: Orden de transporte donde registrar el cambio. Si el objeto
                          ya está bloqueado a una tarea abierta, se reusa esa (idempotente).
                          Si el objeto es transportable y NO se indica orden ni tiene tarea
                          abierta, la tool DEVUELVE ERROR (no crea una orden automáticamente).
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100').
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_update_source({
                'object_name': object_name, 'object_type': object_type,
                'source_code': source_code, 'methods': methods, 'add_interface': add_interface,
                'transport_request': transport_request
            })
            
            extra = f"Updated: {object_name} ({object_type})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error updating source: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_update_source", context, headers, start_time, success, error_message)

    # ===================== Puente abapGit (handler Z /sap/bc/z7bs/abapgit) =====================
    # Reusa el motor de abapGit dentro de SAP para crear/serializar CUALQUIER tipo de objeto
    # (incl. transacciones, que ADT no puede) y mover paquete/OT. paquete y OT POR OBJETO.

    @mcp.tool()
    async def aws_abapgit_serializar(object_type: str, name: str, sap_system_id: str = None) -> str:
        """Serializa un objeto SAP a su representacion abapGit (archivos .xml + fuente).
        Funciona para cualquier tipo que abapGit soporte. Util para luego recrearlo con
        aws_abapgit_crear_objeto. object_type = tipo abapGit (TRAN, PROG, CLAS, TABL, MSAG...)."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service('/serializar', {'object': object_type, 'name': name})
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"serializar {object_type} {name}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_objeto(object_type: str, name: str, package: str, files: list,
                                       transport_request: str = None, sap_system_id: str = None) -> str:
        """Crea/actualiza CUALQUIER objeto que abapGit sabe deserializar (incl. transacciones),
        en `package` y registrando en `transport_request` (POR OBJETO). `files` = lista de
        {filename, content} en formato abapGit (XML + fuente; tal como los entrega
        aws_abapgit_serializar). Para tipos comunes hay helpers (p.ej. aws_abapgit_crear_transaccion).
        package local ($...) = sin OT; transportable = pasar transport_request."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            body = {'object': object_type, 'name': name, 'package': package,
                    'transport': transport_request or '', 'files': files or []}
            result = await sap_client.call_custom_service('/crear_objeto', body)
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"crear_objeto {object_type} {name} -> {package}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_objeto: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_objeto", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_badi(impl: str, badi: str, clase: str, package: str,
                                     transport: str = "", descripcion: str = "",
                                     sap_system_id: str = None) -> str:
        """Registrar una implementacion de BAdI. NO la crea por el MCP: la impl de BAdI
        CLASICA (SXSD) no es viable headless (SXO_IMPL_SAVE es GUI-bound, dispara dynpro
        SAPMSSY0). Devuelve la instruccion para hacerla en SE19. (BAdI nuevas/kernel:
        crear el ENHO con aws_abapgit_crear_objeto object_type=ENHO.)"""
        return (
            "❌ No se puede crear la implementacion de BAdI por el MCP (headless).\n"
            "Motivo: el registro de BAdI clasica (SXO_IMPL_SAVE / SE19) es GUI-bound — "
            "su objeto ALV de filtros dispara un dynpro y no corre sin SAPGUI.\n\n"
            "Hazlo directo en SE19 (pestana 'BAdI clasica'):\n"
            f"  - Implementacion : {impl}\n"
            f"  - BAdI (definicion): {badi}\n"
            f"  - Clase implementadora: {clase}\n"
            f"  - Paquete: {package}\n"
            f"  - Orden: {transport or '(la que uses)'}\n"
            f"  - Texto: {descripcion}\n"
            "Crea la impl, asigna la clase, activa y registra en la orden."
        )

    @mcp.tool()
    async def aws_abapgit_crear_transaccion(tcode: str, program: str, text: str, package: str,
                                            transport_request: str = None, dynpro: str = "1000",
                                            lang: str = "S", sap_system_id: str = None) -> str:
        """Crea una TRANSACCION de tipo reporte (arranca un programa). ADT no puede crear
        transacciones; esto usa el puente abapGit. tcode<=20 chars. `program` debe existir.
        package local ($...) = sin OT; transportable = pasar transport_request. lang = idioma
        del texto (S=español, E=inglés)."""
        import json as _json
        def _esc(v):
            return (str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<abapGit version="v1.0.0" serializer="LCL_OBJECT_TRAN" serializer_version="v1.0.0">\n'
                ' <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n'
                '  <asx:values>\n'
                f'   <TSTC>\n    <TCODE>{_esc(tcode.upper())}</TCODE>\n    <PGMNA>{_esc(program.upper())}</PGMNA>\n'
                f'    <DYPNO>{_esc(dynpro)}</DYPNO>\n    <CINFO>gA==</CINFO>\n   </TSTC>\n'
                f'   <TSTCC>\n    <TCODE>{_esc(tcode.upper())}</TCODE>\n    <S_WEBGUI>1</S_WEBGUI>\n'
                '    <S_WIN32>X</S_WIN32>\n    <S_PLATIN>X</S_PLATIN>\n   </TSTCC>\n'
                f'   <TSTCT>\n    <SPRSL>{_esc(lang)}</SPRSL>\n    <TCODE>{_esc(tcode.upper())}</TCODE>\n'
                f'    <TTEXT>{_esc(text)}</TTEXT>\n   </TSTCT>\n'
                '  </asx:values>\n </asx:abap>\n</abapGit>'
            )
            files = [{'filename': f"{tcode.lower()}.tran.xml", 'content': xml}]
            body = {'object': 'TRAN', 'name': tcode.upper(), 'package': package,
                    'transport': transport_request or '', 'files': files}
            result = await sap_client.call_custom_service('/crear_objeto', body)
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"crear_transaccion {tcode.upper()} -> {program.upper()}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_transaccion: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_transaccion", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_funcion(function_group: str, fms: list, package: str,
                                        descripcion: str = "", transport_request: str = None,
                                        sap_system_id: str = None) -> str:
        """Crea un GRUPO DE FUNCIONES (FUGR) con sus FUNCTION MODULES por el bridge abapGit.
        En abapGit la unidad es el FUGR (los FM viven dentro): este tool arma el bundle
        completo y lo crea. Para AGREGAR un FM a un grupo existente, primero
        aws_abapgit_serializar el FUGR, añade el FM y reusa esto.
        `fms` = lista de {name, source, short_text?, params?}. `source` = cuerpo ABAP entre
        FUNCTION/ENDFUNCTION (la firma sale de params). `params` =
        {importing:[{name,type|dbfield,default?,optional?,reference?}], exporting:[...],
        changing:[...], tables:[{name,dbstruct|type,optional?}], exceptions:[str,...]}.
        package local ($...) = sin OT; transportable = pasar transport_request (la OT)."""
        import json as _json
        from func_fugr import build_fugr
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            FG = function_group.upper()
            gen = build_fugr(FG, fms or [], descripcion)
            files = [{"filename": k, "content": v} for k, v in gen.items()]
            body = {"object": "FUGR", "name": FG, "package": package,
                    "transport": transport_request or "", "files": files}
            result = await sap_client.call_custom_service("/crear_objeto", body)
            if not result.get("ok"):
                success = False; error_message = result.get("error")
            return _format_context_info(context, f"crear_funcion {FG} ({len(fms or [])} FM)\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_funcion: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_funcion", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_leer_tabla(tabla: str, where: str = "", max_rows: int = 200,
                                     mandante: str = "", sap_system_id: str = None) -> str:
        """Lee filas de una tabla SAP (estilo SE16) por SELECT * dinamico (solo lectura).
        `where` = condicion OpenSQL como cadena, p.ej. "TCODE IN ( 'ZA' , 'ZB' )" o
        "BUKRS = '1000'" (literales entre comillas simples). `max_rows` = tope (def 200).
        `mandante` = leer OTRO mandante (CLIENT SPECIFIED); vacio = el de logon. Solo
        aplica a tablas dependientes de mandante (con campo MANDT). Devuelve filas en JSON."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service("/read_table",
                {"tabla": tabla.upper(), "where": where, "maxrows": max_rows, "mandante": mandante},
                base="/sap/bc/z7bsqas/diag")
            if not result.get("ok"):
                success = False; error_message = result.get("error")
                return _format_context_info(context, f"leer_tabla {tabla.upper()}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
            rows = result.get("error") or "[]"  # el bridge pone el JSON de filas en 'error'
            return _format_context_info(context, f"leer_tabla {tabla.upper()} ({result.get('count', 0)} filas)\n") + rows
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"leer_tabla: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_leer_tabla", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_mover_paquete(object_type: str, name: str, package: str, sap_system_id: str = None) -> str:
        """Recoloca un objeto a otro paquete (devclass) via la API TADIR de abapGit.
        object_type = tipo TADIR (PROG, CLAS, TABL, TRAN...)."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service('/mover_paquete',
                {'object': object_type, 'name': name, 'package': package})
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"mover_paquete {object_type} {name} -> {package}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"mover_paquete: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_mover_paquete", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_mover_ot(object_type: str, name: str, transport_request: str, sap_system_id: str = None) -> str:
        """Asigna un objeto a una orden de transporte via la CTS API de abapGit. NOTA: no
        reubica un objeto que ya esta bloqueado en otra orden modificable (limitacion conocida)."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service('/mover_ot',
                {'object': object_type, 'name': name, 'transport': transport_request})
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"mover_ot {object_type} {name} -> {transport_request}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"mover_ot: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_mover_ot", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_serializar_lote(items: list, offset: int = 0, limit: int = 20, sap_system_id: str = None) -> str:
        """Serializa un BLOQUE de objetos (paginado, SIN zip) para armar el backup de un
        requerimiento por partes y evitar payload/timeout. `items` = lista de {object, name}
        (tipo abapGit + nombre). Procesa items[offset:offset+limit], devuelve sus archivos
        abapGit y `next_offset`. El cliente escribe los archivos en el repo y vuelve a llamar
        con next_offset hasta que sea null. El zip se genera aparte (tool fabrica), on-demand."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            items = items or []
            total = len(items)
            chunk = items[offset:offset + limit]
            out_files = []; errors = []
            for it in chunk:
                otype = str(it.get('object') or it.get('object_type') or '').strip()
                oname = str(it.get('name') or '').strip()
                if not otype or not oname:
                    errors.append({'item': it, 'error': 'falta object/name'}); continue
                res = await sap_client.call_custom_service('/serializar', {'object': otype, 'name': oname})
                if res.get('ok'):
                    out_files.extend(res.get('files', []))
                else:
                    errors.append({'object': otype, 'name': oname, 'error': res.get('error')})
            done = offset + len(chunk)
            result = {'ok': len(errors) == 0, 'total': total, 'processed': len(chunk),
                      'next_offset': done if done < total else None,
                      'files': out_files, 'errors': errors}
            if errors:
                success = False; error_message = f"{len(errors)} errores"
            return _format_context_info(context, f"serializar_lote {offset}..{done}/{total}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar_lote: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar_lote", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_serializar_paquete_async(package: str = None, incluir_subpaquetes: bool = True,
                                                   mask: str = None, transport: str = None,
                                                   items: list = None, tipos_incluir: str = None,
                                                   tipos_excluir: str = None, sap_system_id: str = None) -> str:
        """Serializa objetos a un ZIP abapGit de forma ASINCRONA: lanza un job en background dentro
        de SAP y devuelve un `job_id` al instante (no bloquea; sin timeout de sesion ni payload
        gigante). Ideal para miles de objetos, donde el loop objeto-por-objeto
        (aws_abapgit_serializar_lote) truena por conexion/tiempo/payload. Luego consulta el avance
        con aws_abapgit_serializar_estado(job_id) y entrega el ZIP a Gitea con
        aws_abapgit_serializar_a_gitea(job_id, org, repo). Solo lectura (sin OT).

        SELECTOR de que objetos extraer (combinables salvo indicado):
        - `package` (+`incluir_subpaquetes`): todo un paquete (y subpaquetes).
        - `mask`: patron de NOMBRE dentro del paquete, ej. 'ZOSDD*' o 'Z*_REPGER_*'.
        - `transport`: todos los objetos R3TR contenidos en una ORDEN de transporte (y sus tareas).
        - `items`: lista EXPLICITA [{object, name}] (tipo abapGit + nombre); si se pasa, manda sobre package/transport.
        - `tipos_incluir` / `tipos_excluir`: CSV de tipos, ej. 'CLAS,DDLS' o excluir 'TABL,DEVC'.
        Prioridad de fuente: items > transport > package. mask y tipos se aplican al resultado."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            body = {'subpkg': 'X' if incluir_subpaquetes else 'N'}
            if package: body['package'] = package
            if mask: body['mask'] = mask
            if transport: body['transport'] = transport
            if tipos_incluir: body['tipos_in'] = tipos_incluir
            if tipos_excluir: body['tipos_ex'] = tipos_excluir
            if items:
                body['items'] = [{'object': str(i.get('object') or i.get('object_type') or '').strip(),
                                  'name': str(i.get('name') or '').strip()} for i in items]
            result = await sap_client.call_custom_service('/serializar_paquete_async', body)
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"serializar_paquete_async {package}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar_paquete_async: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar_paquete_async", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_serializar_estado(job_id: str, sap_system_id: str = None) -> str:
        """Estado de un job de serializacion asincrona (aws_abapgit_serializar_paquete_async).
        Devuelve status (R=corriendo, D=listo, E=error), total/done (objetos procesados),
        size (bytes del ZIP), nchunks (cuantos chunks bajar) y error si aplica. Cuando status=D,
        baja el ZIP con aws_abapgit_serializar_resultado(job_id, idx) para idx=1..nchunks."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service('/serializar_estado', {'job_id': job_id})
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"serializar_estado {job_id}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar_estado: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar_estado", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_serializar_resultado(job_id: str, idx: int, sap_system_id: str = None) -> str:
        """Baja UN chunk (idx, 1-based) del ZIP de un job de serializacion asincrona ya listo
        (status=D). Devuelve `data` = chunk en base64 (<=2MB) e `idx`. IMPORTANTE para paquetes
        grandes: NO acumules los chunks en el contexto; escribe cada `data` (decodificado de
        base64) directo a un archivo local en modo append binario, e itera idx=1..nchunks (del
        estado). Al terminar tendras el ZIP abapGit del paquete."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            result = await sap_client.call_custom_service('/serializar_resultado', {'job_id': job_id, 'idx': idx})
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"serializar_resultado {job_id} #{idx}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar_resultado: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar_resultado", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_serializar_a_gitea(job_id: str, org: str, repo: str, filename: str = None,
                                             path: str = "code", branch: str = "main",
                                             sap_system_id: str = None) -> str:
        """Baja TODOS los chunks del ZIP de un job de serializacion YA LISTO (status=D), los
        ENSAMBLA dentro del MCP (los bytes NO pasan por el contexto del LLM) y sube el .zip a
        Gitea en `{path}/{filename}.zip` del repo `{org}/{repo}` (rama `{branch}`). Devuelve la
        URL. `filename` por defecto = el paquete del job. Es la forma recomendada de entregar el
        backup de un paquete grande: flujo = aws_abapgit_serializar_paquete_async ->
        aws_abapgit_serializar_estado (esperar D) -> aws_abapgit_serializar_a_gitea."""
        import os, base64, json as _json
        import httpx
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            gitea_base = os.environ.get("GITEA_BASE_URL", "").rstrip("/")
            gitea_tok = os.environ.get("GITEA_API_TOKEN", "")
            if not gitea_base or not gitea_tok:
                raise RuntimeError("Faltan GITEA_BASE_URL / GITEA_API_TOKEN en el entorno del MCP ABAP")
            # 1) estado del job
            st = await sap_client.call_custom_service('/serializar_estado', {'job_id': job_id})
            if (st.get('status') or '').upper() != 'D':
                return _format_context_info(context, "") + _json.dumps(
                    {'ok': False, 'reason': 'job no listo', 'estado': st}, ensure_ascii=False, indent=2)
            nchunks = int(st.get('nchunks') or 0)
            pkg = (st.get('package') or 'paquete').strip()
            # 2) bajar y ensamblar el ZIP (en el proceso, NO en el contexto)
            buf = bytearray()
            for i in range(1, nchunks + 1):
                r = await sap_client.call_custom_service('/serializar_resultado', {'job_id': job_id, 'idx': i})
                buf += base64.b64decode(r.get('data') or '')
            # 3) subir a Gitea
            fname = (filename or pkg).strip().lower().lstrip('$') + '.zip'
            target = f"{path.strip('/')}/{fname}"
            content_b64 = base64.b64encode(bytes(buf)).decode('ascii')
            async with httpx.AsyncClient(base_url=f"{gitea_base}/api/v1",
                                         headers={"Authorization": f"token {gitea_tok}"},
                                         timeout=120.0) as gc:
                ex = await gc.get(f"/repos/{org}/{repo}/contents/{target}", params={"ref": branch})
                body = {"content": content_b64, "branch": branch,
                        "message": f"serializacion async {pkg} ({len(buf)} bytes)"}
                if ex.status_code == 200:
                    body["sha"] = ex.json().get("sha")
                    wr = await gc.put(f"/repos/{org}/{repo}/contents/{target}", json=body)
                else:
                    wr = await gc.post(f"/repos/{org}/{repo}/contents/{target}", json=body)
                if wr.status_code not in (200, 201):
                    raise RuntimeError(f"Gitea PUT {target} -> HTTP {wr.status_code}: {wr.text[:200]}")
            url = f"{gitea_base}/{org}/{repo}/src/branch/{branch}/{target}"
            result = {'ok': True, 'url': url, 'path': target, 'size': len(buf),
                      'chunks': nchunks, 'package': pkg}
            return _format_context_info(context, f"serializar_a_gitea {pkg} -> {target}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"serializar_a_gitea: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_serializar_a_gitea", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_restaurar_repo(org: str, repo: str, package: str, branch: str = "main",
                                         src_path: str = "src", transport_request: str = None,
                                         sap_system_id: str = None) -> str:
        """RESTAURA en SAP el código serializado (abapGit) de un repo Gitea: lee los archivos
        bajo `src_path`, los agrupa por objeto, los ORDENA por dependencias (DDIC→código→CDS→
        servicios→TRAN) y los crea+ACTIVA en `package` (con `transport_request` si es
        transportable). Útil para reusar el backup de un requerimiento en otro cliente.
        El DEVCLASS original se sobreescribe por `package`. Activa por tiers (tabla activa
        antes de la clase que la usa). Devuelve el orden aplicado y el resultado."""
        import os, base64, json as _json
        import httpx
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        TIER = {"DEVC": 5, "DOMA": 10, "DTEL": 20, "PARA": 25, "TABL": 30, "TTYP": 30,
                "STRU": 30, "DRTY": 30, "SHLP": 40, "ENQU": 40, "MSAG": 45, "INTF": 50,
                "CLAS": 55, "FUGR": 60, "PROG": 70, "DDLS": 80, "DCLS": 82, "DDLX": 84,
                "SRVD": 90, "SRVB": 92, "TRAN": 95, "ENHO": 97, "ENHS": 97}
        SKIP = {"DEVC"}  # el paquete lo da el caller; no recreamos packages
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            gitea_base = os.environ.get("GITEA_BASE_URL", "").rstrip("/")
            gitea_tok = os.environ.get("GITEA_API_TOKEN", "")
            if not gitea_base or not gitea_tok:
                raise RuntimeError("Faltan GITEA_BASE_URL / GITEA_API_TOKEN en el entorno del MCP ABAP")

            files = {}
            async with httpx.AsyncClient(base_url=f"{gitea_base}/api/v1",
                                         headers={"Authorization": f"token {gitea_tok}"},
                                         timeout=60.0) as gc:
                stack = [src_path.strip("/")]
                while stack:
                    cur = stack.pop()
                    r = await gc.get(f"/repos/{org}/{repo}/contents/{cur}", params={"ref": branch})
                    if r.status_code == 404:
                        continue
                    r.raise_for_status()
                    for e in r.json():
                        if e.get("type") == "dir":
                            stack.append(e["path"])
                        elif e.get("type") == "file":
                            rf = await gc.get(f"/repos/{org}/{repo}/contents/{e['path']}", params={"ref": branch})
                            rf.raise_for_status()
                            files[e["path"]] = base64.b64decode(rf.json()["content"]).decode("utf-8", "replace")

            groups = {}
            for path, content in files.items():
                fn = path.rsplit("/", 1)[-1]
                if fn.startswith("."):
                    continue  # .abapgit.xml y dotfiles
                parts = fn.split(".")
                if len(parts) < 3:
                    continue  # formato abapGit = <name>.<type>.<ext>
                oname = parts[0].upper(); otype = parts[1].upper()
                if not oname or not otype.isalnum() or otype in SKIP:
                    continue
                groups.setdefault((otype, oname), []).append({"filename": fn, "content": content})

            if not groups:
                return _format_context_info(context, "") + f"No se encontraron objetos abapGit bajo '{src_path}' en {org}/{repo}@{branch}."

            ordered = sorted(groups, key=lambda k: (TIER.get(k[0], 100), k[0], k[1]))
            items = [{"object": t, "name": n, "files": groups[(t, n)]} for (t, n) in ordered]
            orden = [f"{t} {n}" for (t, n) in ordered]

            body = {"package": package, "transport": transport_request or "", "items": items}
            result = await sap_client.call_custom_service("/restaurar", body)
            if not result.get("ok"):
                success = False; error_message = result.get("error")
            out = {"orden_aplicado": orden, "objetos": len(items), "resultado": result}
            return _format_context_info(context, f"restaurar_repo {org}/{repo}@{branch}/{src_path} -> {package} ({len(items)} objetos)\n") + _json.dumps(out, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"restaurar_repo: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_restaurar_repo", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_clase_mensajes(name: str, descripcion: str, mensajes: list, package: str,
                                               transport_request: str = None, lang: str = "S", sap_system_id: str = None) -> str:
        """Crea una clase de mensajes (MSAG / SE91) con sus mensajes. `mensajes` = lista de
        {num, texto} (num 0-999). ADT no crea MSAG; usa el bridge abapGit. package local
        ($...) = sin OT; transportable = pasar transport_request. lang = idioma (S/E)."""
        import json as _json
        def _esc(v): return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            nm = name.upper(); t100 = ""
            for m in (mensajes or []):
                raw = str(m.get("num", "")).strip()
                try: num = f"{int(raw):03d}"
                except (TypeError, ValueError): num = (raw or "000")[:3].rjust(3, "0")
                t100 += (f"<T100><SPRSL>{_esc(lang)}</SPRSL><ARBGB>{_esc(nm)}</ARBGB>"
                         f"<MSGNR>{_esc(num)}</MSGNR><TEXT>{_esc(m.get('texto', ''))}</TEXT></T100>")
            xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
                   '<abapGit version="v1.0.0" serializer="LCL_OBJECT_MSAG" serializer_version="v1.0.0">\n'
                   ' <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0"><asx:values>\n'
                   f'  <T100A><ARBGB>{_esc(nm)}</ARBGB><MASTERLANG>{_esc(lang)}</MASTERLANG><STEXT>{_esc(descripcion)}</STEXT></T100A>\n'
                   f'  <T100>{t100}</T100>\n </asx:values></asx:abap>\n</abapGit>')
            files = [{"filename": f"{name.lower()}.msag.xml", "content": xml}]
            body = {"object": "MSAG", "name": nm, "package": package, "transport": transport_request or "", "files": files}
            result = await sap_client.call_custom_service("/crear_objeto", body)
            if not result.get("ok"): success = False; error_message = result.get("error")
            return _format_context_info(context, f"crear_clase_mensajes {nm} ({len(mensajes or [])} msj)\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_clase_mensajes: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_clase_mensajes", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_textos_programa(programa: str, textos: list, lang: str = "S", sap_system_id: str = None) -> str:
        """Crea/actualiza los TEXT ELEMENTS de un PROGRAMA. `textos` = lista de {id, key, text}:
          id='R' título (key=''), id='I' text symbol (key=código de 3 chars), id='H' encabezado.
        Reemplaza el text pool del idioma `lang` (INSERT TEXTPOOL + activación).

        IMPORTANTE — TEXTOS DE SELECCIÓN (id='S'): el tool NO los gestiona. Los 'S' que
        envíes se IGNORAN (y los existentes se preservan, no se borran). Los textos de
        selección deben hacerse A MANO en SE38 (pestaña 'Textos de selección'): el flag
        'Referencia a DDIC' no vive en el textpool (no se limpia headless) y SAP los
        regenera al modificar el programa. El título (R) y los símbolos (I) SÍ funcionan
        por el tool."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            body = {"object": "PROG", "name": programa.upper(), "lang": lang, "textos": textos or []}
            result = await sap_client.call_custom_service("/textos", body)
            if not result.get("ok"): success = False; error_message = result.get("error")
            return _format_context_info(context, f"textos {programa.upper()} ({len(textos or [])})\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_textos_programa: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_textos_programa", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_generar_mantenimiento(tabla: str, function_group: str, transport_request: str = None,
                                                screen: str = "0001", tipo: str = "1", sap_system_id: str = None) -> str:
        """Genera el diálogo de mantenimiento SM30/SE54 de una tabla Z (pantalla editable
        para cargar datos). `function_group` = grupo de funciones a generar (Z*). tipo='1'
        un paso, '2' dos pasos. Tabla transportable → pasar transport_request. (FM
        TABLE_MAINTENANCE_GENERATOR vía el handler.)"""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            body = {"tabla": tabla.upper(), "fgrp": function_group.upper(),
                    "transport": transport_request or "", "screen": screen, "tipo": tipo}
            result = await sap_client.call_custom_service("/generar_mantenimiento", body)
            if not result.get("ok"): success = False; error_message = result.get("error")
            return _format_context_info(context, f"generar_mantenimiento {tabla.upper()} -> {function_group.upper()}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"generar_mantenimiento: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_generar_mantenimiento", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_crear_mantenimiento(tabla: str, package: str, transport_request: str = None,
                                              function_group: str = None, sap_system_id: str = None) -> str:
        """Crea HEADLESS el mantenimiento SM30 de una tabla transparente Z (lo que SE54 hace en
        GUI, que NO es llamable sin diálogo). Lee los campos de la tabla (DDIF_FIELDINFO_GET),
        genera el grupo de funciones de mantenimiento de UN paso (includes + 2 FMs + pantalla
        de resumen con una columna por campo y la clave agrupada) y lo deserializa+activa por el
        bridge abapGit, luego registra el SM30 en TVDIR. `function_group` por defecto = nombre de
        la tabla. package local ($...) = sin OT; transportable → pasar transport_request.
        Reversible con aws_abapgit_borrar_mantenimiento."""
        import json as _json
        from maint_fugr import build_maint_fugr
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            TAB = tabla.upper(); FG = (function_group or tabla).upper()
            cmp = await sap_client.call_custom_service("/campos_tabla", {"tabla": TAB})
            if not cmp.get("ok"):
                raise RuntimeError(f"campos_tabla: {cmp.get('error')}")
            fields = _json.loads(cmp.get("error") or "[]")
            ncols = len([x for x in fields if (x.get("datatype") or x.get("DATATYPE")) != "CLNT"])
            gen = build_maint_fugr(TAB, fields, FG)
            files = [{"filename": k, "content": v} for k, v in gen.items()]
            crea = await sap_client.call_custom_service("/crear_objeto",
                {"object": "FUGR", "name": FG, "package": package, "transport": transport_request or "", "files": files})
            if not crea.get("ok"):
                raise RuntimeError(f"crear FUGR: {crea.get('error')}")
            reg = await sap_client.call_custom_service("/registrar_sm30",
                {"tabla": TAB, "fgrp": FG, "package": package})
            if not reg.get("ok"):
                success = False; error_message = reg.get("error")
            out = {"ok": bool(reg.get("ok")), "tabla": TAB, "function_group": FG, "campos": ncols,
                   "fugr_creado": crea.get("ok"), "registro_sm30": reg.get("ok")}
            return _format_context_info(context, f"crear_mantenimiento {TAB} -> SM30 (fgrp {FG}, {ncols} campos)\n") + _json.dumps(out, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"crear_mantenimiento: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_crear_mantenimiento", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_borrar_mantenimiento(tabla: str, package: str, function_group: str = None,
                                               transport_request: str = None, sap_system_id: str = None) -> str:
        """Inverso de aws_abapgit_crear_mantenimiento: quita el registro SM30 (TVDIR/TVIMF) y
        borra el grupo de funciones de mantenimiento. Hay que desregistrar ANTES de borrar la
        tabla (la dependencia TVDIR bloquea el borrado). `function_group` por defecto = tabla."""
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            TAB = tabla.upper(); FG = (function_group or tabla).upper()
            dreg = await sap_client.call_custom_service("/desregistrar_sm30", {"tabla": TAB})
            dele = await sap_client.call_custom_service("/borrar_objeto",
                {"object": "FUGR", "name": FG, "package": package, "transport": transport_request or ""})
            if not dele.get("ok"): success = False; error_message = dele.get("error")
            out = {"ok": bool(dele.get("ok")), "tabla": TAB, "function_group": FG,
                   "desregistro_sm30": dreg.get("ok"), "fugr_borrado": dele.get("ok")}
            return _format_context_info(context, f"borrar_mantenimiento {TAB}\n") + _json.dumps(out, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"borrar_mantenimiento: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_borrar_mantenimiento", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abapgit_borrar_objeto(object_type: str, name: str, package: str = "",
                                        transport_request: str = None, sap_system_id: str = None) -> str:
        """Borra CUALQUIER objeto que abapGit sepa eliminar, vía el motor abapGit dentro de
        SAP (handler ZCL_ABAPGIT_OBJECT_<tipo>->delete, endpoint /borrar_objeto del bridge).

        Es el COMPLEMENTO de aws_abap_cb_delete_object (ADT REST): úsalo para los tipos que
        ADT NO puede borrar por REST — p.ej. IWVB (Vocabulary Annotation) y demás objetos de
        workbench/GUI cuyo controlador ADT es solo-lectura (POST/DELETE devuelven 405). Para
        objetos lockables por ADT (CLAS/INTF/PROG/DDLS/DCLS/SRVD/...) puedes usar cualquiera
        de las dos; abapGit respeta el mismo patrón de OT (default transport).

        Args:
            object_type: Tipo abapGit del objeto (IWVB, FUGR, PROG, CLAS, TABL, DTEL, SRVB, ...).
            name: Nombre del objeto tal como está en TADIR (R3TR <object_type> <name>).
            package: Paquete del objeto. El handler abapGit lo usa para localizar/transportar;
                     obligatorio salvo objetos locales ($TMP).
            transport_request: OT donde registrar el borrado. Omitir para objetos locales ($...).
            sap_system_id: ID de sistema SAP opcional (header x-sap-system-id si se omite).
        """
        import json as _json
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            body = {'object': object_type.upper(), 'name': name.upper(),
                    'package': (package or '').upper(), 'transport': transport_request or ''}
            result = await sap_client.call_custom_service('/borrar_objeto', body)
            if not result.get('ok'):
                success = False; error_message = result.get('error')
            return _format_context_info(context, f"borrar_objeto {object_type} {name}\n") + _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"borrar_objeto: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abapgit_borrar_objeto", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_check_syntax(object_name: str, object_type: str, source_code: str = None, sap_system_id: str = None) -> str:
        """
        Check syntax of ABAP object
        
        Args:
            object_name: Name of the object
            object_type: Type of object
            source_code: Optional source code to check
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_check_syntax(object_name, object_type, source_code)
            
            extra = f"Syntax Check: {object_name} ({object_type})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error checking syntax: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_check_syntax", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_activate_object(object_name: str = None, object_type: str = None, objects: list = None, sap_system_id: str = None) -> str:
        """
        Activate ABAP objects
        
        Args:
            object_name: Name of single object to activate
            object_type: Type of single object
            objects: List of objects to activate
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_activate_object({
                'object_name': object_name, 'object_type': object_type, 'objects': objects
            })
            
            extra = f"Activate: {object_name or 'Multiple objects'}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error activating: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_activate_object", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_publish_service_binding(binding_name: str, unpublish: bool = False, sap_system_id: str = None) -> str:
        """
        Publish (or unpublish) the Local Service Endpoint of a Service Binding (SRVB) via ADT.

        Dispara la acción Publish del editor de Service Binding (business services /
        publishjobs), para OData V2 y V4 UI. Resuelve servicio/versión/tipo desde la
        propia metadata del binding. Devuelve estado, URL del servicio y, en caso de
        fallo, el mensaje real del backend (SEVERITY/SHORT_TEXT/LONG_TEXT).

        Args:
            binding_name: Nombre del Service Binding (p.ej. ZOSSD_RPGR_O4)
            unpublish: True para despublicar en vez de publicar
            sap_system_id: ID de sistema SAP opcional (header x-sap-system-id si se omite)
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_publish_service_binding({
                'binding_name': binding_name, 'unpublish': unpublish
            })
            extra = f"{'Unpublish' if unpublish else 'Publish'} SRVB: {binding_name}\n"
            return _format_context_info(context, extra) + result
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error publishing service binding: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_publish_service_binding", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_delete_object(object_name: str = None, object_type: str = None, transport_request: str = None, object_uri: str = None, sap_system_id: str = None) -> str:
        """
        Borra un objeto de repositorio ABAP vía ADT REST (patrón lock -> delete -> unlock).

        Si se pasa `object_uri`, se borra ESA URI tal cual (ya url-encoded), sin re-resolver
        ni re-encodear: es la vía recomendada para IWVB y objetos de workbench, usando la
        URI que expone search_object (p.ej. /sap/bc/adt/vit/wb/object_type/iwvb/object_name/
        Z_OPLACCTGDOCITEMCUBE_VAN%20%20%20%20%20%20%200001). Si no se pasa object_uri, se
        resuelve la URI por (object_name, object_type): IWVB/workbench vía search_object,
        y SRVB/SRVD/DDLS/DCLS/CLAS/INTF/PROG/etc. con el resolvedor de get_source/update_source.
        corrNr se envía SOLO si el objeto es transportable (no local $TMP) y se indicó orden.
        Devuelve el mensaje real del backend en caso de fallo (SEVERITY/texto).

        Caso de uso: eliminar una Vocabulary Annotation (IWVB) huérfana que SE80/SE84 no
        exponen y que bloquea la activación de un Service Binding.

        Args:
            object_name: Nombre del objeto a borrar (p.ej. Z_OPLACCTGDOCITEMCUBE_VAN).
                          Opcional si se pasa object_uri.
            object_type: Tipo del objeto ('IWVB','SRVB','SRVD','DDLS','DCLS','CLAS','INTF','PROG',...).
                          Requerido salvo que se pase object_uri.
            transport_request: Orden de transporte donde registrar el borrado. Solo se usa
                          si el objeto es transportable; para objetos locales ($TMP) se omite.
            object_uri: URI ADT completa y ya url-encoded del objeto a borrar. Si viene, se
                          usa tal cual y se saltan object_name/object_type y el resolvedor.
            sap_system_id: ID de sistema SAP opcional. Si se omite se usa el header
                          x-sap-system-id. NO se fuerza ningún default.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_delete_object({
                'object_name': object_name, 'object_type': object_type,
                'transport_request': transport_request, 'object_uri': object_uri
            })
            extra = f"Delete: {object_name or object_uri} ({object_type or 'by-uri'})\n"
            return _format_context_info(context, extra) + result
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error deleting object: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_delete_object", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_get_service_binding_status(binding_name: str, sap_system_id: str = None) -> str:
        """
        Get publish status (published yes/no) and service URL of a Service Binding (SRVB).

        Args:
            binding_name: Nombre del Service Binding (p.ej. ZOSSD_RPGR_O4)
            sap_system_id: ID de sistema SAP opcional (header x-sap-system-id si se omite)
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_service_binding_status({
                'binding_name': binding_name
            })
            extra = f"SRVB status: {binding_name}\n"
            return _format_context_info(context, extra) + result
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting service binding status: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_service_binding_status", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_run_atc_check(object_name: str = None, object_type: str = None, package_name: str = None, include_subpackages: bool = False, transport_number: str = None, variant: str = None, include_documentation: bool = True, summary_mode: bool = False, sap_system_id: str = None) -> str:
        """
        Run ATC check on ABAP object
        
        Args:
            object_name: Name of object to check
            object_type: Type of object
            package_name: Package name to check
            include_subpackages: Include subpackages
            transport_number: Transport request number
            variant: ATC variant
            include_documentation: Include documentation
            summary_mode: Summary mode
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            from sap_types.sap_types import ATCCheckArgs
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_run_atc_check(ATCCheckArgs(
                object_name=object_name, object_type=object_type, package_name=package_name,
                include_subpackages=include_subpackages, transport_number=transport_number,
                variant=variant, include_documentation=include_documentation
            ), summary_mode=summary_mode)
            
            extra = f"ATC Check: {object_name or package_name or 'General'}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error running ATC: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_run_atc_check", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_run_unit_tests(object_name: str, object_type: str = "CLAS", with_coverage: bool = False, sap_system_id: str = None) -> str:
        """
        Run unit tests for ABAP object
        
        Args:
            object_name: Name of object to test
            object_type: Type of object (default: CLAS)
            with_coverage: Include coverage data
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_run_unit_tests(object_name, object_type, with_coverage)
            
            extra = f"Unit Tests: {object_name} ({object_type})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error running tests: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_run_unit_tests", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_get_test_classes(class_name: str, object_type: str = "CLAS", sap_system_id: str = None) -> str:
        """
        Get test classes for ABAP class
        
        Args:
            class_name: Name of class
            object_type: Type of object (default: CLAS)
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_test_classes(class_name, object_type)
            
            extra = f"Test Classes: {class_name}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting test classes: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_test_classes", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_get_migration_analysis(object_name: str, object_type: str, sap_system_id: str = None) -> str:
        """
        Get migration analysis for ABAP object
        
        Args:
            object_name: Name of object
            object_type: Type of object
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_migration_analysis(object_name, object_type)
            
            extra = f"Migration Analysis: {object_name} ({object_type})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting migration analysis: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_migration_analysis", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_create_or_update_test_class(class_name: str, methods: list = None, source_code: str = None, sap_system_id: str = None) -> str:
        """
        Create or update local test class (ABAP Unit) in /includes/testclasses of a class.

        Args:
            class_name: Clase bajo prueba (la clase principal ya debe existir).
            methods: Lista de métodos de test [{name, implementation}]. Cada uno se genera
                     como `<name> FOR TESTING` en un ltc_<clase> DURATION SHORT RISK LEVEL
                     HARMLESS. Para tests con helper/TYPES/CLASS-DATA/setup usa source_code.
            source_code: Include de test COMPLETO (uno o más CLASS ... DEFINITION FOR TESTING
                         / IMPLEMENTATION). Tiene prioridad sobre methods. Recomendado para
                         tests reales. La clase se llama internamente; el objeto sigue siendo
                         la clase principal (el resultado se rotula <class_name>_TEST).
            sap_system_id: Optional SAP system identifier. Si falta, usa x-sap-system-id.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}

        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)

            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_create_or_update_test_class(class_name, methods, source_code)

            n = len(methods) if methods else 0
            extra = f"Test Class: {class_name} ({'source_code' if source_code else f'{n} methods'})\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error with test class: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_create_or_update_test_class", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_activate_objects_batch(objects: list, sap_system_id: str = None) -> str:
        """
        Activate multiple ABAP objects in batch
        
        Args:
            objects: List of objects to activate
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_activate_objects_batch({'objects': objects})
            
            extra = f"Batch Activate: {len(objects)} objects\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error batch activating: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_activate_objects_batch", context, headers, start_time, success, error_message)
    
    @mcp.tool()
    async def aws_abap_cb_get_transport_requests(username: str = None, sap_system_id: str = None) -> str:
        """
        Get transport requests
        
        Args:
            username: Optional username filter
            sap_system_id: Optional SAP system identifier (e.g., 'S4H-100'). 
                          If not provided, uses x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_get_transport_requests(username)
            
            extra = f"Transport Requests: {username or 'current user'}\n"
            return _format_context_info(context, extra) + result
            
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error getting transports: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_get_transport_requests", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_list_transport_objects(transport_request: str, sap_system_id: str = None) -> str:
        """
        List the objects contained in a transport request (read-only).

        Args:
            transport_request: Transport request number (e.g. 'DS4K930376')
            sap_system_id: Optional SAP system identifier. If not provided, uses
                          x-sap-system-id header or DEFAULT_SAP_SYSTEM_ID env var.
        """
        start_time = time.time()
        success = True
        error_message = None
        context = {}
        headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            tool_handlers = ToolHandlers(sap_client)
            result = await tool_handlers.handle_list_transport_objects(transport_request)
            return _format_context_info(context, f"Transport objects: {transport_request}\n") + result
        except Exception as e:
            success = False
            error_message = str(e)
            logger.error(f"Error listing transport objects: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_list_transport_objects", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_create_domain(name: str, description: str, data_type: str, package_name: str = None, length: int = None, decimals: int = None, conversion_exit: str = None, output_length: int = None, output_style: str = None, value_table: str = None, fixed_values: list = None, transport_request: str = None, sap_system_id: str = None) -> str:
        """Create + activate a DDIC Domain (DOMA).

        data_type p.ej. CHAR/NUMC/DEC/INT4/DATS. fixed_values: lista de
        {"low":valor,"text":texto,"position":n,"high":opcional}. value_table
        opcional (si viene, valueTableExists=true). transport_request si el
        paquete no es $TMP.

        output_length: longitud de salida (columna) del dominio; si se omite toma
        la longitud del tipo. output_style: estilo de salida (código 2 chars, def
        "00"). La longitud de salida SOLO existe en el dominio (un DTEL predefinido
        no la tiene).
        """
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            result = await ToolHandlers(sap_client).handle_create_domain({
                'name': name, 'description': description, 'data_type': data_type,
                'package_name': package_name or "$TMP", 'length': length, 'decimals': decimals,
                'conversion_exit': conversion_exit, 'output_length': output_length,
                'output_style': output_style, 'value_table': value_table,
                'fixed_values': fixed_values, 'transport_request': transport_request,
            })
            return _format_context_info(context, f"Domain: {name}\n") + result
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"Error creating domain: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_create_domain", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_create_data_element(name: str, description: str, package_name: str = None, domain_name: str = None, data_type: str = None, length: int = None, decimals: int = None, field_labels: dict = None, transport_request: str = None, sap_system_id: str = None) -> str:
        """Create + activate a DDIC Data Element (DTEL).

        Pasa domain_name (typeKind=domain) O data_type+length (predefined).
        field_labels: {"short":..,"medium":..,"long":..,"heading":..}.
        """
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            result = await ToolHandlers(sap_client).handle_create_data_element({
                'name': name, 'description': description, 'package_name': package_name or "$TMP",
                'domain_name': domain_name, 'data_type': data_type, 'length': length,
                'decimals': decimals, 'field_labels': field_labels, 'transport_request': transport_request,
            })
            return _format_context_info(context, f"DataElement: {name}\n") + result
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"Error creating data element: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_create_data_element", context, headers, start_time, success, error_message)

    @mcp.tool()
    async def aws_abap_cb_create_table(name: str, description: str, fields: list, key_fields: list, package_name: str = None, table_category: str = "TRANSP", delivery_class: str = "A", transport_request: str = None, sap_system_id: str = None) -> str:
        """Create + activate a DDIC transparent Table (TABL).

        fields: [{"name":..,"data_element":..,"not_null":"true|false"}]; key_fields:
        [nombres clave]. transport_request si el paquete no es $TMP.
        """
        start_time = time.time(); success = True; error_message = None; context = {}; headers = {}
        try:
            sap_client, context, headers = await _get_auth_context(sap_system_id)
            from server.tool_handlers import ToolHandlers
            result = await ToolHandlers(sap_client).handle_create_table({
                'name': name, 'description': description, 'package_name': package_name or "$TMP",
                'table_category': table_category, 'delivery_class': delivery_class,
                'fields': fields, 'key_fields': key_fields, 'transport_request': transport_request,
            })
            return _format_context_info(context, f"Table: {name}\n") + result
        except Exception as e:
            success = False; error_message = str(e); logger.error(f"Error creating table: {e}")
            return f"❌ Error: {str(e)}"
        finally:
            _track_usage("aws_abap_cb_create_table", context, headers, start_time, success, error_message)

    logger.info("Registered 18 SAP tools with principal propagation support (incl. DDIC domain/data_element/table)")
