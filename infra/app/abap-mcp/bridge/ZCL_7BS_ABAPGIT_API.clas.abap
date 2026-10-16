CLASS zcl_7bs_abapgit_api DEFINITION
  PUBLIC
  CREATE PUBLIC.

  PUBLIC SECTION.
    INTERFACES if_http_extension.

  PRIVATE SECTION.
    TYPES: BEGIN OF ty_file,
             filename TYPE string,
             content  TYPE string,
           END OF ty_file.
    TYPES ty_files TYPE STANDARD TABLE OF ty_file WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_item_in,
             object TYPE string,
             name   TYPE string,
             files  TYPE ty_files,
           END OF ty_item_in.
    TYPES ty_items_in TYPE STANDARD TABLE OF ty_item_in WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_text,
             id   TYPE string,
             key  TYPE string,
             text TYPE string,
           END OF ty_text.
    TYPES ty_textos TYPE STANDARD TABLE OF ty_text WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_in,
             object    TYPE string,
             name      TYPE string,
             package   TYPE string,
             transport TYPE string,
             files     TYPE ty_files,
             items     TYPE ty_items_in,
             lang      TYPE string,
             textos    TYPE ty_textos,
             tabla     TYPE string,
             fgrp      TYPE string,
             screen    TYPE string,
             tipo      TYPE string,
             where     TYPE string,
             maxrows   TYPE i,
             mandante  TYPE string,
             subpkg    TYPE string,
             job_id    TYPE string,
             idx       TYPE i,
             mask      TYPE string,
             tipos_in  TYPE string,
             tipos_ex  TYPE string,
           END OF ty_in.
    TYPES: BEGIN OF ty_out,
             ok            TYPE abap_bool,
             object        TYPE string,
             name          TYPE string,
             package       TYPE string,
             old_package   TYPE string,
             new_package   TYPE string,
             old_transport TYPE string,
             new_transport TYPE string,
             count         TYPE i,
             files         TYPE ty_files,
             error         TYPE string,
             job_id        TYPE string,
             status        TYPE string,
             total         TYPE i,
             done          TYPE i,
             size          TYPE i,
             nchunks       TYPE i,
             idx           TYPE i,
             data          TYPE string,
           END OF ty_out.
    TYPES: BEGIN OF ty_run,
             item TYPE zif_abapgit_definitions=>ty_item,
             obj  TYPE REF TO zif_abapgit_object,
             xml  TYPE REF TO zif_abapgit_xml_input,
           END OF ty_run.

    METHODS move_package    IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS move_transport  IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS serialize_object IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS create_object   IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS restore_objects IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS delete_object   IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS set_textpool    IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS gen_mantenimiento IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS check_sm30      IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS registrar_sm30  IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS desregistrar_sm30 IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS campos_tabla    IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS deserialize_and_activate
      IMPORTING it_items TYPE ty_items_in iv_package TYPE string iv_transport TYPE string
      RAISING zcx_abapgit_exception.
    METHODS resolve_task
      IMPORTING iv_transport TYPE trkorr RETURNING VALUE(rv_task) TYPE trkorr
      RAISING zcx_abapgit_exception.
    METHODS resolve_order
      IMPORTING iv_transport TYPE trkorr RETURNING VALUE(rv_order) TYPE trkorr.
    METHODS create_sxci     IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS read_table      IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS read_textpool   IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    " Serializacion asincrona de paquete (job background + ZIP en INDX). Ver ZCL_7BS_SER_ASYNC.
    METHODS ser_pkg_async   IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS ser_pkg_estado  IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
    METHODS ser_pkg_result  IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out RAISING zcx_abapgit_exception.
ENDCLASS.


CLASS zcl_7bs_abapgit_api IMPLEMENTATION.

  METHOD if_http_extension~handle_request.
    DATA: lv_path TYPE string, ls_in TYPE ty_in, ls_out TYPE ty_out, lv_code TYPE i.
    lv_path = server->request->get_header_field( '~path_info' ).
    TRY.
        /ui2/cl_json=>deserialize( EXPORTING json = server->request->get_cdata( ) CHANGING data = ls_in ).
        CASE lv_path.
          WHEN '/mover_paquete'.       ls_out = move_package( ls_in ).    lv_code = 200.
          WHEN '/mover_ot'.            ls_out = move_transport( ls_in ).  lv_code = 200.
          WHEN '/serializar'.          ls_out = serialize_object( ls_in ). lv_code = 200.
          WHEN '/serializar_paquete_async'. ls_out = ser_pkg_async( ls_in ). lv_code = 200.
          WHEN '/serializar_estado'.   ls_out = ser_pkg_estado( ls_in ). lv_code = 200.
          WHEN '/serializar_resultado'. ls_out = ser_pkg_result( ls_in ). lv_code = 200.
          WHEN '/crear_objeto'.        ls_out = create_object( ls_in ).   lv_code = 200.
          WHEN '/restaurar'.           ls_out = restore_objects( ls_in ). lv_code = 200.
          WHEN '/borrar_objeto'.       ls_out = delete_object( ls_in ).   lv_code = 200.
          WHEN '/textos'.              ls_out = set_textpool( ls_in ).    lv_code = 200.
          WHEN '/generar_mantenimiento'. ls_out = gen_mantenimiento( ls_in ). lv_code = 200.
          WHEN '/check_sm30'.          ls_out = check_sm30( ls_in ).      lv_code = 200.
          WHEN '/registrar_sm30'.      ls_out = registrar_sm30( ls_in ).  lv_code = 200.
          WHEN '/desregistrar_sm30'.   ls_out = desregistrar_sm30( ls_in ). lv_code = 200.
          WHEN '/campos_tabla'.        ls_out = campos_tabla( ls_in ).    lv_code = 200.
          WHEN '/read_table'.          ls_out = read_table( ls_in ).      lv_code = 200.
          WHEN '/read_textpool'.       ls_out = read_textpool( ls_in ).   lv_code = 200.
          WHEN OTHERS.
            ls_out-ok = abap_false. ls_out-error = |unknown path: { lv_path }|. lv_code = 404.
        ENDCASE.
      CATCH zcx_abapgit_exception INTO DATA(lx_git).
        CLEAR ls_out. ls_out-ok = abap_false. ls_out-error = lx_git->get_text( ). lv_code = 422.
      CATCH cx_root INTO DATA(lx_root).
        CLEAR ls_out. ls_out-ok = abap_false. ls_out-error = lx_root->get_text( ). lv_code = 500.
    ENDTRY.
    server->response->set_content_type( 'application/json; charset=utf-8' ).
    server->response->set_cdata( /ui2/cl_json=>serialize( data = ls_out compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ) ).
    server->response->set_status( code = lv_code reason = 'OK' ).
  ENDMETHOD.

  METHOD ser_pkg_async.
    " Lanza el job background. El SELECTOR (paquete/subpkg, mascara, orden de
    " transporte, lista explicita de items, filtro por tipo incluir/excluir) se arma
    " en un spec y viaja a la clase. Devuelve job_id al instante.
    DATA ls_spec TYPE zcl_7bs_ser_async=>ty_spec.
    ls_spec-package   = is_in-package.
    ls_spec-subpkg    = COND #( WHEN is_in-subpkg = 'N' THEN abap_false ELSE abap_true ).
    ls_spec-mask      = is_in-mask.
    ls_spec-transport = is_in-transport.
    ls_spec-types_in  = is_in-tipos_in.
    ls_spec-types_ex  = is_in-tipos_ex.
    LOOP AT is_in-items INTO DATA(ls_it).
      APPEND VALUE #( object = ls_it-object obj_name = ls_it-name ) TO ls_spec-items.
    ENDLOOP.
    rs_out-job_id = zcl_7bs_ser_async=>start( ls_spec ).
    rs_out-status = 'R'. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD ser_pkg_estado.
    DATA(ls) = zcl_7bs_ser_async=>status( CONV #( is_in-job_id ) ).
    rs_out-job_id = is_in-job_id. rs_out-status = ls-status.
    rs_out-total = ls-total. rs_out-done = ls-done. rs_out-size = ls-size.
    rs_out-nchunks = ls-nchunks. rs_out-package = ls-package. rs_out-error = ls-errtxt.
    rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD ser_pkg_result.
    " Un chunk (idx, 1-based) del ZIP en base64.
    DATA(lx) = zcl_7bs_ser_async=>chunk( iv_jobid = CONV #( is_in-job_id ) iv_index = is_in-idx ).
    rs_out-job_id = is_in-job_id. rs_out-idx = is_in-idx.
    rs_out-size = xstrlen( lx ).
    rs_out-data = cl_http_utility=>encode_x_base64( lx ).
    rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD move_package.
    DATA: lv_object TYPE tadir-object, lv_obj_name TYPE tadir-obj_name, lv_package TYPE devclass.
    lv_object = is_in-object. lv_obj_name = is_in-name. lv_package = is_in-package.
    DATA(li_tadir) = zcl_abapgit_factory=>get_tadir( ).
    rs_out-object = is_in-object. rs_out-name = is_in-name.
    rs_out-old_package = li_tadir->get_object_package( iv_object = lv_object iv_obj_name = lv_obj_name ).
    li_tadir->insert_single( iv_object = lv_object iv_obj_name = lv_obj_name iv_package = lv_package ).
    rs_out-new_package = is_in-package. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD move_transport.
    " Reasigna la entrada de transporte del objeto a la orden/tarea DESTINO. Replica
    " el "alta natural" de SAP cuando la orden VIEJA esta liberada: NO se intenta
    " quitar el objeto de ella (no se puede modificar una orden liberada y ademas
    " queda como historial); solo se da de alta en la destino. E070-TRSTATUS
    " liberado = O (release iniciado) / R / N (ambos liberados).
    DATA: lv_object TYPE tadir-object, lv_obj_name TYPE tadir-obj_name,
          lv_transport TYPE trkorr, lv_old TYPE trkorr, ls_item TYPE zif_abapgit_definitions=>ty_item,
          lv_stat TYPE e070-trstatus.
    lv_object = is_in-object. lv_obj_name = is_in-name. lv_transport = resolve_task( CONV #( is_in-transport ) ).

    " Validar que la orden/tarea DESTINO no este liberada (no se le pueden agregar
    " objetos). resolve_task no valida el status cuando ya le pasan una tarea, asi
    " que lo comprobamos aqui y damos un error claro en vez del cript. de CTS.
    SELECT SINGLE trstatus FROM e070 WHERE trkorr = @lv_transport INTO @lv_stat.
    IF sy-subrc = 0 AND lv_stat CA 'ORN'.
      zcx_abapgit_exception=>raise(
        |La orden/tarea destino { is_in-transport } esta liberada (status { lv_stat }); | &&
        |indica una orden modificable.| ).
    ENDIF.

    ls_item-obj_type = lv_object. ls_item-obj_name = lv_obj_name.
    rs_out-object = is_in-object. rs_out-name = is_in-name.
    DATA(li_cts) = zcl_abapgit_factory=>get_cts_api( ).
    DATA(lv_pkg) = zcl_abapgit_factory=>get_tadir( )->get_object_package( iv_object = lv_object iv_obj_name = lv_obj_name ).
    TRY. lv_old = li_cts->get_transport_for_object( ls_item ). CATCH zcx_abapgit_exception. CLEAR lv_old. ENDTRY.
    rs_out-old_transport = lv_old.

    " Solo quitar el objeto de la orden vieja si NO esta liberada. Si lo esta, se
    " omite (queda congelado ahi como historial) y solo se da de alta en la destino.
    IF lv_old IS NOT INITIAL AND lv_old <> lv_transport.
      CLEAR lv_stat.
      SELECT SINGLE trstatus FROM e070 WHERE trkorr = @lv_old INTO @lv_stat.
      IF sy-subrc = 0 AND lv_stat NA 'ORN'.
        li_cts->insert_transport_object( iv_object = lv_object iv_obj_name = lv_obj_name iv_package = lv_pkg iv_transport = lv_old iv_mode = 'D' ).
      ENDIF.
    ENDIF.
    li_cts->insert_transport_object( iv_object = lv_object iv_obj_name = lv_obj_name iv_package = lv_pkg iv_transport = lv_transport iv_mode = 'I' ).
    rs_out-new_transport = is_in-transport. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD serialize_object.
    DATA: ls_item TYPE zif_abapgit_definitions=>ty_item, lv_class TYPE string, li_obj TYPE REF TO zif_abapgit_object.
    ls_item-obj_type = is_in-object. ls_item-obj_name = is_in-name.
    lv_class = |ZCL_ABAPGIT_OBJECT_{ is_in-object }|.
    DATA(lo_files) = zcl_abapgit_objects_files=>new( ls_item ).
    CREATE OBJECT li_obj TYPE (lv_class) EXPORTING is_item = ls_item iv_language = sy-langu io_files = lo_files.
    DATA(lo_xml) = NEW zcl_abapgit_xml_output( ).
    li_obj->serialize( lo_xml ).
    lo_files->add_xml( ii_xml = lo_xml is_metadata = li_obj->get_metadata( ) ).
    LOOP AT lo_files->get_files( ) INTO DATA(ls_f).
      APPEND VALUE #( filename = ls_f-filename content = zcl_abapgit_convert=>xstring_to_string_utf8( ls_f-data ) ) TO rs_out-files.
    ENDLOOP.
    rs_out-object = is_in-object. rs_out-name = is_in-name. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD resolve_task.
    " Las tools cb_* pasan corrNr=<orden> a ADT y ADT resuelve/usa la tarea solo.
    " abapGit (corr_insert -> RS_CORR_INSERT) exige una TAREA, no la orden: si nos
    " pasan la orden, derivamos una tarea modificable del usuario bajo ella. NO se
    " crean ordenes ni tareas; si ya nos pasan una tarea, se usa tal cual. Si la
    " orden no tiene tarea del usuario -> error claro (crearla en SE09 y reintentar).
    DATA: lv_func TYPE e070-trfunction, lv_stat TYPE e070-trstatus.
    rv_task = iv_transport.
    IF iv_transport IS INITIAL.
      RETURN.                                  " objeto local / $TMP, sin transporte
    ENDIF.
    SELECT SINGLE trfunction FROM e070 WHERE trkorr = @iv_transport INTO @lv_func.
    IF sy-subrc <> 0.
      zcx_abapgit_exception=>raise( |{ iv_transport } no existe en E070| ).
    ENDIF.
    " Si ya es una tarea (S/Q/R), usarla; solo derivamos cuando es orden (K/W).
    IF lv_func <> zif_abapgit_cts_api=>c_transport_type-wb_request
      AND lv_func <> zif_abapgit_cts_api=>c_transport_type-cust_request.
      RETURN.
    ENDIF.
    DATA(lt_tasks) = zcl_abapgit_factory=>get_cts_api( )->read_request_and_tasks( iv_transport ).
    LOOP AT lt_tasks INTO DATA(ls_t) WHERE as4user = sy-uname AND trkorr <> iv_transport.
      SELECT SINGLE trstatus FROM e070 WHERE trkorr = @ls_t-trkorr INTO @lv_stat.
      IF sy-subrc = 0 AND ( lv_stat = 'D' OR lv_stat = 'L' ).
        rv_task = ls_t-trkorr.
        RETURN.
      ENDIF.
    ENDLOOP.
    zcx_abapgit_exception=>raise(
      |La orden { iv_transport } no tiene una tarea modificable de { sy-uname }; | &&
      |crea una tarea bajo la orden (SE09) y reintenta.| ).
  ENDMETHOD.

  METHOD resolve_order.
    " Devuelve la ORDEN a fijar como default (zcl_abapgit_default_transport->set
    " -> TR_TASK_SET iv_order). Si nos pasan una tarea (S/Q/R), devolvemos su orden
    " padre (strkorr); si ya es una orden (K/W) o no se reconoce, se devuelve igual.
    DATA lv_func TYPE e070-trfunction.
    rv_order = iv_transport.
    IF iv_transport IS INITIAL.
      RETURN.
    ENDIF.
    SELECT SINGLE trfunction FROM e070 WHERE trkorr = @iv_transport INTO @lv_func.
    IF sy-subrc = 0
      AND lv_func <> zif_abapgit_cts_api=>c_transport_type-wb_request
      AND lv_func <> zif_abapgit_cts_api=>c_transport_type-cust_request.
      SELECT SINGLE strkorr FROM e070 WHERE trkorr = @iv_transport INTO @rv_order.
    ENDIF.
  ENDMETHOD.

  METHOD deserialize_and_activate.
    DATA: lt_run TYPE STANDARD TABLE OF ty_run, ls_item TYPE zif_abapgit_definitions=>ty_item,
          lv_class TYPE string, lv_package TYPE devclass, lv_transport TYPE trkorr,
          lt_files TYPE zif_abapgit_git_definitions=>ty_files_tt,
          lt_steps TYPE zif_abapgit_objects=>ty_deserialization_step_tt, li_obj TYPE REF TO zif_abapgit_object.
    lv_package = iv_package. lv_transport = resolve_task( CONV #( iv_transport ) ).
    DATA(lv_order) = resolve_order( CONV #( iv_transport ) ).
    DATA(li_log) = NEW zcl_abapgit_log( ).
    zcl_abapgit_objects_activation=>clear( ).
    LOOP AT it_items INTO DATA(ls_it).
      CLEAR: ls_item, lt_files.
      ls_item-obj_type = ls_it-object. ls_item-obj_name = ls_it-name.
      DATA(lo_files) = zcl_abapgit_objects_files=>new( ls_item ).
      LOOP AT ls_it-files INTO DATA(ls_in_f).
        APPEND VALUE #( path = '/' filename = ls_in_f-filename data = zcl_abapgit_convert=>string_to_xstring_utf8( ls_in_f-content ) ) TO lt_files.
      ENDLOOP.
      lo_files->set_files( lt_files ).
      lv_class = |ZCL_ABAPGIT_OBJECT_{ ls_it-object }|.
      CREATE OBJECT li_obj TYPE (lv_class) EXPORTING is_item = ls_item iv_language = sy-langu io_files = lo_files.
      APPEND VALUE #( item = ls_item obj = li_obj xml = lo_files->read_xml( ) ) TO lt_run.
    ENDLOOP.
    APPEND zif_abapgit_object=>gc_step_id-early TO lt_steps.
    APPEND zif_abapgit_object=>gc_step_id-ddic  TO lt_steps.
    APPEND zif_abapgit_object=>gc_step_id-abap  TO lt_steps.
    APPEND zif_abapgit_object=>gc_step_id-late  TO lt_steps.
    APPEND zif_abapgit_object=>gc_step_id-lxe   TO lt_steps.
    " Transporte headless: la mayoria de handlers (p.ej. TRAN -> corr_insert +
    " RPY_TRANSACTION_INSERT con suppress_corr_insert) NO usan iv_transport sino el
    " DEFAULT del usuario (E070USE, via TR_TASK_SET). Lo fijamos a la ORDEN como hace
    " abapGit para evitar el popup/"no se puede determinar tarea"; se restaura siempre.
    DATA(lo_dt) = NEW zcl_abapgit_default_transport( ).
    TRY.
        IF lv_order IS NOT INITIAL.
          lo_dt->zif_abapgit_default_transport~set( lv_order ).
        ENDIF.
        LOOP AT lt_steps INTO DATA(lv_step).
          LOOP AT lt_run INTO DATA(ls_run).
            DATA(lt_obj_steps) = ls_run-obj->get_deserialize_steps( ).
            READ TABLE lt_obj_steps TRANSPORTING NO FIELDS WITH KEY table_line = lv_step.
            IF sy-subrc = 0.
              ls_run-obj->deserialize( iv_package = lv_package io_xml = ls_run-xml iv_step = lv_step ii_log = li_log iv_transport = lv_transport ).
            ENDIF.
          ENDLOOP.
          IF lv_step = zif_abapgit_object=>gc_step_id-ddic.
            zcl_abapgit_objects_activation=>activate( iv_ddic = abap_true ii_log = li_log ).
          ENDIF.
        ENDLOOP.
        zcl_abapgit_objects_activation=>activate( iv_ddic = abap_false ii_log = li_log ).
        lo_dt->zif_abapgit_default_transport~reset( ).
      CLEANUP.
        TRY.
            lo_dt->zif_abapgit_default_transport~reset( ).
          CATCH zcx_abapgit_exception.
        ENDTRY.
    ENDTRY.
    COMMIT WORK AND WAIT.
  ENDMETHOD.

  METHOD create_object.
    DATA lt_items TYPE ty_items_in.
    " BAdI clasica (SXCI): el deserialize de abapGit usa SXO_IMPL_SAVE con
    " genflag=X que dispara un dynpro (SAPMSSY0) -> falla headless. Ruta propia
    " sin generacion (genflag=false) para crearla por el bridge sin GUI.
    IF is_in-object = 'SXCI'.
      rs_out = create_sxci( is_in ).
      RETURN.
    ENDIF.
    APPEND VALUE #( object = is_in-object name = is_in-name files = is_in-files ) TO lt_items.
    deserialize_and_activate( it_items = lt_items iv_package = is_in-package iv_transport = is_in-transport ).
    rs_out-object = is_in-object. rs_out-name = is_in-name. rs_out-package = is_in-package.
    rs_out-new_transport = is_in-transport. rs_out-count = 1. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD create_sxci.
    " Impl de BAdI CLASICA (SXCI): NO es viable crearla por el MCP/bridge headless.
    " SXO_IMPL_SAVE es GUI-bound (requiere el objeto ALV de filtros, cuya instancia
    " dispara un dynpro SAPMSSY0 sin SAPGUI). Registrar en SE19 (pestania BAdI clasica).
    rs_out-object = is_in-object. rs_out-name = is_in-name. rs_out-ok = abap_false.
    zcx_abapgit_exception=>raise(
      |SXCI (BAdI clasica) no es viable por el MCP: SXO_IMPL_SAVE es GUI-bound. | &&
      |Registra la implementacion en SE19 (pestania BAdI clasica).| ).
  ENDMETHOD.

  METHOD restore_objects.
    deserialize_and_activate( it_items = is_in-items iv_package = is_in-package iv_transport = is_in-transport ).
    rs_out-package = is_in-package. rs_out-new_transport = is_in-transport.
    rs_out-count = lines( is_in-items ). rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD delete_object.
    DATA: ls_item TYPE zif_abapgit_definitions=>ty_item, lv_class TYPE string,
          li_obj TYPE REF TO zif_abapgit_object, lv_package TYPE devclass, lv_transport TYPE trkorr.
    ls_item-obj_type = is_in-object. ls_item-obj_name = is_in-name.
    lv_package = is_in-package. lv_transport = resolve_task( CONV #( is_in-transport ) ).
    DATA(lv_order) = resolve_order( CONV #( is_in-transport ) ).
    lv_class = |ZCL_ABAPGIT_OBJECT_{ is_in-object }|.
    DATA(lo_files) = zcl_abapgit_objects_files=>new( ls_item ).
    CREATE OBJECT li_obj TYPE (lv_class) EXPORTING is_item = ls_item iv_language = sy-langu io_files = lo_files.
    " El borrado GENERICO (get_generic->delete -> corr_insert -> insert_transport_object
    " SIN iv_transport) NO usa el iv_transport que le pasamos: se apoya en el DEFAULT
    " transport del usuario. Sin fijarlo, RS_CORR_INSERT aborta con "No task for editing
    " objects can be determined". Fijamos el default a la orden recibida (mismo patron
    " que deserialize_and_activate, verificado e2e) y lo reseteamos en CLEANUP.
    DATA(lo_dt) = NEW zcl_abapgit_default_transport( ).
    TRY.
        IF lv_order IS NOT INITIAL.
          lo_dt->zif_abapgit_default_transport~set( lv_order ).
        ENDIF.
        li_obj->delete( iv_package = lv_package iv_transport = lv_transport ii_log = NEW zcl_abapgit_log( ) ).
        lo_dt->zif_abapgit_default_transport~reset( ).
      CLEANUP.
        TRY.
            lo_dt->zif_abapgit_default_transport~reset( ).
          CATCH zcx_abapgit_exception.
        ENDTRY.
    ENDTRY.
    COMMIT WORK AND WAIT.
    " Devolvemos la orden/tarea resueltas para confirmar que el corrNr llego al handler.
    rs_out-object = is_in-object. rs_out-name = is_in-name.
    rs_out-old_transport = lv_order. rs_out-new_transport = lv_transport.
    rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD set_textpool.
    DATA: lt_tp TYPE STANDARD TABLE OF textpool, lt_cur TYPE STANDARD TABLE OF textpool,
          ls_tp TYPE textpool, lv_prog TYPE programm, lv_lang TYPE spras,
          lv_title TYPE string, lv_skipped TYPE i.
    lv_prog = is_in-name.
    lv_lang = COND #( WHEN is_in-lang IS NOT INITIAL THEN is_in-lang ELSE sy-langu ).
    " Los TEXTOS DE SELECCION (id='S') NO los gestiona el tool: se hacen a MANO en
    " SE38. Motivo: el flag "Referencia a DDIC" NO vive en el textpool (no se puede
    " limpiar headless) y los selection-texts se regeneran al tocar el programa.
    " Aqui se IGNORAN los 'S' entrantes y se PRESERVAN los existentes (INSERT TEXTPOOL
    " reemplaza TODO el pool). Solo se gestionan R (titulo) e I (simbolos), que si van.
    LOOP AT is_in-textos INTO DATA(t).
      IF t-id = 'S'.
        lv_skipped = lv_skipped + 1.
        CONTINUE.
      ENDIF.
      CLEAR ls_tp. ls_tp-id = t-id. ls_tp-key = t-key. ls_tp-entry = t-text. ls_tp-length = strlen( t-text ).
      APPEND ls_tp TO lt_tp.
      IF t-id = 'R'.                 " entrada de titulo (key vacia)
        lv_title = t-text.
      ENDIF.
    ENDLOOP.
    " Preservar los selection-texts (S) ya existentes, para que el reemplazo del pool
    " no los borre. El tool nunca los modifica.
    READ TEXTPOOL lv_prog INTO lt_cur LANGUAGE lv_lang.
    LOOP AT lt_cur INTO DATA(cur) WHERE id = 'S'.
      APPEND cur TO lt_tp.
    ENDLOOP.
    " TITULO (R): no se fija por textpool ni TRDIRT; se hace con RPY_PROGRAM_UPDATE
    " (como abapGit), que reescribe la fuente con el titulo nuevo. Best-effort: si
    " falla, no rompe los textos. Se hace ANTES del textpool (orden de abapGit).
    IF lv_title IS NOT INITIAL.
      DATA: lt_src TYPE abaptxt255_tab, lv_titlec TYPE repti.
      FIELD-SYMBOLS <ttab> TYPE any.
      lv_titlec = lv_title.
      READ REPORT lv_prog INTO lt_src.
      IF sy-subrc = 0.
        ASSIGN ('(SAPLSIFP)TTAB') TO <ttab>.  " workaround: limpia header-line (bug RPY_PROGRAM_UPDATE)
        IF sy-subrc = 0. CLEAR <ttab>. ENDIF.
        CALL FUNCTION 'RPY_PROGRAM_UPDATE'
          EXPORTING program_name = lv_prog title_string = lv_titlec save_inactive = space
          TABLES source_extended = lt_src
          EXCEPTIONS OTHERS = 0.
      ENDIF.
    ENDIF.
    INSERT TEXTPOOL lv_prog FROM lt_tp LANGUAGE lv_lang STATE 'I'.
    zcl_abapgit_objects_activation=>clear( ).
    zcl_abapgit_objects_activation=>add( iv_type = 'REPT' iv_name = lv_prog ).
    zcl_abapgit_objects_activation=>activate( ii_log = NEW zcl_abapgit_log( ) ).
    COMMIT WORK AND WAIT.
    " Echo de diagnostico: lo que el bridge realmente recibio/escribio (id/key/entry),
    " para cazar payloads con key<->text desalineado (texto "movido").
    rs_out-error = /ui2/cl_json=>serialize( data = lt_tp compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-object = is_in-object. rs_out-name = is_in-name.
    rs_out-count = lines( is_in-textos ) - lv_skipped. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD gen_mantenimiento.
    DATA: lv_tab TYPE tabname, lv_fgrp TYPE tlibg-area, lv_corr TYPE trkorr, lv_scr TYPE c LENGTH 4, lv_type TYPE c LENGTH 1.
    lv_tab = is_in-tabla. lv_fgrp = is_in-fgrp. lv_corr = resolve_task( CONV #( is_in-transport ) ).
    lv_scr  = COND #( WHEN is_in-screen IS NOT INITIAL THEN is_in-screen ELSE '0001' ).
    lv_type = COND #( WHEN is_in-tipo IS NOT INITIAL THEN is_in-tipo ELSE '1' ).
    IF zcl_abapgit_factory=>get_function_module( )->function_exists( 'TABLE_MAINTENANCE_GENERATOR' ) = abap_false.
      zcx_abapgit_exception=>raise( 'TABLE_MAINTENANCE_GENERATOR no disponible/llamable; usa crear_mantenimiento (FUGR+TVDIR).' ).
    ENDIF.
    TRY.
        CALL FUNCTION 'TABLE_MAINTENANCE_GENERATOR'
          EXPORTING authority_check = 'X' corr_number = lv_corr tabname = lv_tab
                    maint_function_group = lv_fgrp maint_screen_number = lv_scr maint_screen_type = lv_type
          EXCEPTIONS OTHERS = 1.
      CATCH cx_sy_dyn_call_illegal_func INTO DATA(lx_dyn).
        zcx_abapgit_exception=>raise( |SM30 no llamable headless (GUI): | && lx_dyn->get_text( ) ).
    ENDTRY.
    IF sy-subrc <> 0.
      zcx_abapgit_exception=>raise( |TABLE_MAINTENANCE_GENERATOR subrc { sy-subrc } / { sy-msgid }{ sy-msgno } para { lv_tab }| ).
    ENDIF.
    COMMIT WORK AND WAIT.
    rs_out-object = is_in-tabla. rs_out-name = is_in-fgrp. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD check_sm30.
    DATA lv_tab TYPE tabname.
    lv_tab = is_in-tabla.
    SELECT * FROM tvdir INTO TABLE @DATA(lt_dir) WHERE tabname = @lv_tab.
    SELECT * FROM tvimf INTO TABLE @DATA(lt_imf) WHERE tabname = @lv_tab.
    rs_out-object = lv_tab.
    rs_out-count  = lines( lt_dir ).
    rs_out-error  = |TVDIR=| && /ui2/cl_json=>serialize( data = lt_dir compress = abap_false )
                 && | @@TVIMF=| && /ui2/cl_json=>serialize( data = lt_imf compress = abap_false ).
    rs_out-ok     = abap_true.
  ENDMETHOD.

  METHOD registrar_sm30.
    " Registra el SM30 de una tabla en TVDIR (lo que SE54 escribe), para enganchar
    " el diálogo (FUGR ya creado por el bridge) a la tabla. Mantenimiento de UN paso
    " (TYPE=1, overview screen). Escritura directa a metadata, reversible (desregistrar).
    DATA ls_dir TYPE tvdir.
    ls_dir-tabname  = is_in-tabla.
    ls_dir-area     = is_in-fgrp.
    ls_dir-devclass = is_in-package.
    ls_dir-type     = COND #( WHEN is_in-tipo IS NOT INITIAL THEN is_in-tipo ELSE '1' ).
    ls_dir-liste    = COND #( WHEN is_in-screen IS NOT INITIAL THEN is_in-screen ELSE '0001' ).
    ls_dir-detail   = 0.
    ls_dir-occurs   = 0.
    ls_dir-bastab   = 'X'.
    ls_dir-gendate  = sy-datum.
    ls_dir-gentime  = sy-uzeit.
    ls_dir-flag     = 'X'.
    MODIFY tvdir FROM ls_dir.
    COMMIT WORK AND WAIT.
    rs_out-object = is_in-tabla. rs_out-name = is_in-fgrp. rs_out-count = sy-dbcnt. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD desregistrar_sm30.
    " Inverso de registrar_sm30: quita el registro SM30 de la tabla (TVDIR/TVIMF).
    " Necesario para poder borrar luego la tabla (la dependencia TVDIR la bloquea).
    DATA lv_tab TYPE tabname.
    lv_tab = is_in-tabla.
    DELETE FROM tvdir WHERE tabname = lv_tab.
    DATA(lv_cnt) = sy-dbcnt.
    DELETE FROM tvimf WHERE tabname = lv_tab.
    COMMIT WORK AND WAIT.
    rs_out-object = lv_tab. rs_out-count = lv_cnt. rs_out-ok = abap_true.
  ENDMETHOD.

  METHOD campos_tabla.
    " Devuelve los campos de la tabla (nombre, clave, longitud salida, tipo, texto)
    " vía DDIF_FIELDINFO_GET (headless) — insumo para templatizar la pantalla SM30.
    DATA: lt_dfies TYPE STANDARD TABLE OF dfies, lv_tab TYPE ddobjname.
    lv_tab = is_in-tabla.
    CALL FUNCTION 'DDIF_FIELDINFO_GET'
      EXPORTING tabname = lv_tab langu = sy-langu
      TABLES dfies_tab = lt_dfies
      EXCEPTIONS OTHERS = 1.
    IF sy-subrc <> 0.
      zcx_abapgit_exception=>raise( |DDIF_FIELDINFO_GET subrc { sy-subrc } para { lv_tab }| ).
    ENDIF.
    rs_out-object = is_in-tabla.
    rs_out-error  = /ui2/cl_json=>serialize( data = lt_dfies compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-count  = lines( lt_dfies ).
    rs_out-ok     = abap_true.
  ENDMETHOD.

  METHOD read_table.
    " Lectura generica de tabla (estilo SE16): SELECT * dinamico con WHERE opcional
    " (cadena, p.ej. "TCODE IN ( 'A' , 'B' )") y tope de filas. JSON de filas en 'error'.
    DATA: lr TYPE REF TO data, lv_tab TYPE tabname, lv_rows TYPE i, lv_up TYPE string, lv_mand TYPE mandt.
    FIELD-SYMBOLS <t> TYPE STANDARD TABLE.
    lv_tab  = is_in-tabla.
    lv_up   = to_upper( is_in-tabla ).
    " Denylist de tablas sensibles (claves/credenciales). OpenSQL SELECT no chequea
    " S_TABU, asi que bloqueamos lo critico a mano.
    IF lv_up CP 'USR*' OR lv_up CP 'USH*' OR lv_up CP 'USRACL*'
      OR lv_up = 'RFCDES' OR lv_up = 'RFCSYSACL' OR lv_up = 'RZLLITAB'.
      zcx_abapgit_exception=>raise( |Tabla { lv_up } bloqueada (sensible)| ).
    ENDIF.
    " 'where' obligatorio: no permitir volcar la tabla entera.
    IF is_in-where IS INITIAL.
      zcx_abapgit_exception=>raise( 'where obligatorio (no se permite leer la tabla completa)' ).
    ENDIF.
    lv_rows = COND #( WHEN is_in-maxrows > 0 THEN is_in-maxrows ELSE 200 ).
    CREATE DATA lr TYPE STANDARD TABLE OF (lv_tab).
    ASSIGN lr->* TO <t>.
    " Con mandante -> CLIENT SPECIFIED + filtro MANDT (otro mandante que el de logon).
    " Solo tablas dependientes de mandante (campo MANDT); en client-independent falla.
    IF is_in-mandante IS NOT INITIAL.
      lv_mand = is_in-mandante.
      SELECT * FROM (lv_tab) CLIENT SPECIFIED INTO TABLE @<t> UP TO @lv_rows ROWS
        WHERE mandt = @lv_mand AND (is_in-where).
    ELSE.
      SELECT * FROM (lv_tab) INTO TABLE <t> UP TO lv_rows ROWS WHERE (is_in-where).
    ENDIF.
    rs_out-object = is_in-tabla.
    rs_out-count  = lines( <t> ).
    rs_out-error  = /ui2/cl_json=>serialize( data = <t> compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-ok     = abap_true.
  ENDMETHOD.

  METHOD read_textpool.
    " Diagnostico: devuelve el textpool crudo (ID/KEY/ENTRY/LENGTH/SPLIT) para ver
    " como se guarda un selection-text con referencia DDIC vs literal.
    DATA: lt_tp TYPE STANDARD TABLE OF textpool, lv_prog TYPE programm, lv_lang TYPE spras.
    lv_prog = is_in-name.
    lv_lang = COND #( WHEN is_in-lang IS NOT INITIAL THEN is_in-lang ELSE sy-langu ).
    READ TEXTPOOL lv_prog INTO lt_tp LANGUAGE lv_lang.
    rs_out-object = is_in-name.
    rs_out-count  = lines( lt_tp ).
    rs_out-error  = /ui2/cl_json=>serialize( data = lt_tp compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-ok     = abap_true.
  ENDMETHOD.

ENDCLASS.
