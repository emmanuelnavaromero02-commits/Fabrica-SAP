CLASS zcl_7bs_sapcfg_api DEFINITION
  PUBLIC
  CREATE PUBLIC.

  PUBLIC SECTION.
    INTERFACES if_http_extension.

  PRIVATE SECTION.
    " ---- contrato JSON (generico, agnostico de modulo) ----
    TYPES: BEGIN OF ty_field,
             name  TYPE string,
             value TYPE string,
           END OF ty_field.
    TYPES ty_fields TYPE STANDARD TABLE OF ty_field WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_rec,
             recnumber TYPE i,
             fields    TYPE ty_fields,
           END OF ty_rec.
    TYPES ty_recs TYPE STANDARD TABLE OF ty_rec WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_block,
             tablename TYPE string,
             records   TYPE ty_recs,
           END OF ty_block.
    TYPES ty_blocks TYPE STANDARD TABLE OF ty_block WITH DEFAULT KEY.
    TYPES: BEGIN OF ty_in,
             bcset_id  TYPE string,
             text      TYPE string,
             component TYPE string,
             category  TYPE string,
             simulate  TYPE abap_bool,
             transport TYPE string,
             tablename TYPE string,
             where     TYPE string,
             max_rows  TYPE i,
             blocks    TYPE ty_blocks,
           END OF ty_in.
    TYPES: BEGIN OF ty_out,
             ok        TYPE abap_bool,
             bcset_id  TYPE string,
             rc        TYPE i,
             transport TYPE string,
             count     TYPE i,
             data      TYPE string,   " payload JSON (filas / valores / log)
             error     TYPE string,
           END OF ty_out.
    " linea de BCSET_IDS de SCPR_ACTIVATE_BCSETS_REMOTE (tipo local SCPR_PARNT):
    " parent/bcset. Compatibilidad por layout en parametro TABLES.
    TYPES: BEGIN OF ty_parnt,
             bcset  TYPE scpr_id,
             parent TYPE scpr_id,
           END OF ty_parnt.

    METHODS read_table     IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out.
    METHODS bcset_read     IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out.
    METHODS bcset_write    IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out.
    METHODS bcset_activate IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out.
    METHODS ddic_fields    IMPORTING is_in TYPE ty_in RETURNING VALUE(rs_out) TYPE ty_out.
ENDCLASS.


CLASS zcl_7bs_sapcfg_api IMPLEMENTATION.

  METHOD if_http_extension~handle_request.
    DATA: lv_path TYPE string, ls_in TYPE ty_in, ls_out TYPE ty_out, lv_code TYPE i.
    lv_path = server->request->get_header_field( '~path_info' ).
    TRY.
        /ui2/cl_json=>deserialize( EXPORTING json = server->request->get_cdata( ) CHANGING data = ls_in ).
        CASE lv_path.
          WHEN '/read_table'.     ls_out = read_table( ls_in ).     lv_code = 200.
          WHEN '/bcset_read'.     ls_out = bcset_read( ls_in ).     lv_code = 200.
          WHEN '/bcset_write'.    ls_out = bcset_write( ls_in ).    lv_code = 200.
          WHEN '/bcset_activate'. ls_out = bcset_activate( ls_in ). lv_code = 200.
          WHEN '/ddic'.           ls_out = ddic_fields( ls_in ).    lv_code = 200.
          WHEN '/ping'.           ls_out-ok = abap_true. ls_out-data = 'sapcfg ok'. lv_code = 200.
          WHEN OTHERS.
            ls_out-ok = abap_false. ls_out-error = |unknown path: { lv_path }|. lv_code = 404.
        ENDCASE.
      CATCH cx_root INTO DATA(lx_root).
        CLEAR ls_out. ls_out-ok = abap_false. ls_out-error = lx_root->get_text( ). lv_code = 500.
    ENDTRY.
    server->response->set_content_type( 'application/json; charset=utf-8' ).
    server->response->set_cdata( /ui2/cl_json=>serialize( data = ls_out compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ) ).
    server->response->set_status( code = lv_code reason = 'OK' ).
  ENDMETHOD.

  METHOD read_table.
    " Lee entradas actuales de una tabla (para el diff del preview). Dinamico y generico.
    DATA: lr TYPE REF TO data, lv_tab TYPE tabname, lv_rows TYPE i.
    FIELD-SYMBOLS <t> TYPE STANDARD TABLE.
    lv_tab  = is_in-tablename.
    lv_rows = COND #( WHEN is_in-max_rows > 0 THEN is_in-max_rows ELSE 200 ).
    TRY.
        CREATE DATA lr TYPE STANDARD TABLE OF (lv_tab).
        ASSIGN lr->* TO <t>.
        IF is_in-where IS NOT INITIAL.
          SELECT * FROM (lv_tab) INTO TABLE <t> UP TO lv_rows ROWS WHERE (is_in-where).
        ELSE.
          SELECT * FROM (lv_tab) INTO TABLE <t> UP TO lv_rows ROWS.
        ENDIF.
        rs_out-count = lines( <t> ).
        rs_out-data  = /ui2/cl_json=>serialize( data = <t> compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
        rs_out-ok    = abap_true.
      CATCH cx_root INTO DATA(lx).
        rs_out-ok = abap_false. rs_out-error = lx->get_text( ).
    ENDTRY.
  ENDMETHOD.

  METHOD ddic_fields.
    " Campos DDIC de una tabla/estructura (DDIF_FIELDINFO_GET) — insumo para mapear spec->campos.
    DATA: lt_dfies TYPE STANDARD TABLE OF dfies, lv_name TYPE ddobjname.
    lv_name = is_in-tablename.
    CALL FUNCTION 'DDIF_FIELDINFO_GET'
      EXPORTING tabname = lv_name langu = sy-langu
      TABLES dfies_tab = lt_dfies
      EXCEPTIONS OTHERS = 1.
    IF sy-subrc <> 0.
      rs_out-ok = abap_false. rs_out-error = |DDIF_FIELDINFO_GET subrc { sy-subrc } para { lv_name }|. RETURN.
    ENDIF.
    rs_out-count = lines( lt_dfies ).
    rs_out-data  = /ui2/cl_json=>serialize( data = lt_dfies compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-ok    = abap_true.
  ENDMETHOD.

  METHOD bcset_read.
    " Lee un BC-Set existente -> sus valores (para diff/idempotencia y para aprender el formato).
    DATA: lt_vals TYPE STANDARD TABLE OF scprvals,
          lt_reca TYPE STANDARD TABLE OF scprreca,
          lv_id   TYPE scprattr-id,
          lv_text TYPE scprtext-text,
          lv_comp TYPE scprattr-component,
          lv_min  TYPE scprattr-minrelease,
          lv_max  TYPE scprattr-maxrelease.
    lv_id = is_in-bcset_id.
    CALL FUNCTION 'SCPR_EXT_BCSET_READ'
      EXPORTING  bcset_id   = lv_id
                 category   = CONV scpr_ctgry( is_in-category )
      IMPORTING  bcset_text = lv_text
                 component  = lv_comp
                 minrelease = lv_min
                 maxrelease = lv_max
      TABLES     values     = lt_vals
                 recattr    = lt_reca
      EXCEPTIONS bcset_dont_exist = 1
                 OTHERS           = 2.
    IF sy-subrc <> 0.
      rs_out-ok = abap_false. rs_out-error = |SCPR_EXT_BCSET_READ subrc { sy-subrc }|. RETURN.
    ENDIF.
    rs_out-bcset_id = lv_id.
    rs_out-count    = lines( lt_vals ).
    rs_out-data     = /ui2/cl_json=>serialize( data = lt_vals compress = abap_true pretty_name = /ui2/cl_json=>pretty_mode-low_case ).
    rs_out-ok       = abap_true.
  ENDMETHOD.

  METHOD bcset_write.
    " Construye un BC-Set value-based desde {tabla -> registros -> campos} (modelo SCPRVALS,
    " generico para cualquier modulo) y lo guarda. La activacion va aparte (bcset_activate).
    DATA: lt_vals TYPE STANDARD TABLE OF scprvals, ls_v TYPE scprvals,
          lt_reca TYPE STANDARD TABLE OF scprreca, ls_r TYPE scprreca,
          lv_id   TYPE scprattr-id.
    lv_id = is_in-bcset_id.
    LOOP AT is_in-blocks INTO DATA(ls_b).
      LOOP AT ls_b-records INTO DATA(ls_rec).
        CLEAR ls_r.
        ls_r-id         = lv_id.
        ls_r-tablename  = ls_b-tablename.
        ls_r-recnumber  = ls_rec-recnumber.
        ls_r-objectname = ls_b-tablename.
        ls_r-objecttype = 'TABU'.
        APPEND ls_r TO lt_reca.
        LOOP AT ls_rec-fields INTO DATA(ls_f).
          CLEAR ls_v.
          ls_v-id        = lv_id.
          ls_v-tablename = ls_b-tablename.
          ls_v-recnumber = ls_rec-recnumber.
          ls_v-fieldname = ls_f-name.
          ls_v-value     = ls_f-value.
          APPEND ls_v TO lt_vals.
        ENDLOOP.
      ENDLOOP.
    ENDLOOP.
    CALL FUNCTION 'SCPR_EXT_BCSET_WRITE'
      EXPORTING  bcset_id   = lv_id
                 bcset_text = CONV scprtext-text( is_in-text )
                 component  = CONV scprattr-component( is_in-component )
                 category   = CONV scpr_ctgry( is_in-category )
      TABLES     values     = lt_vals
                 recattr    = lt_reca
      EXCEPTIONS user_abort                = 1
                 error_in_transport_layer  = 2
                 other_error               = 3
                 OTHERS                    = 4.
    IF sy-subrc <> 0.
      rs_out-ok = abap_false. rs_out-error = |SCPR_EXT_BCSET_WRITE subrc { sy-subrc }|. RETURN.
    ENDIF.
    rs_out-bcset_id = lv_id.
    rs_out-count    = lines( lt_vals ).
    rs_out-ok       = abap_true.
  ENDMETHOD.

  METHOD bcset_activate.
    " Activa un BC-Set: carga los valores en las tablas destino. simulate='X' = dry-run
    " (insumo del gate de preview). transport = orden customizing (W). RFC-enabled headless.
    DATA: lt_ids   TYPE STANDARD TABLE OF ty_parnt, ls_id TYPE ty_parnt,
          lv_proto TYPE scpr_handl,
          lv_rc    TYPE scpracst,
          lv_cust  TYPE trkorr,
          lv_corr  TYPE e070-trkorr.
    ls_id-bcset = is_in-bcset_id.
    APPEND ls_id TO lt_ids.
    lv_corr = is_in-transport.
    CALL FUNCTION 'SCPR_ACTIVATE_BCSETS_REMOTE'
      EXPORTING  activation_type  = 1
                 simulation_on    = COND #( WHEN is_in-simulate = abap_true THEN 'X' ELSE ' ' )
                 task_number_cust = lv_corr
                 transport_off    = 'N'
      IMPORTING  proto_handle     = lv_proto
                 rc_activ         = lv_rc
                 task_cust_exp    = lv_cust
      TABLES     bcset_ids        = lt_ids.
    rs_out-bcset_id  = is_in-bcset_id.
    rs_out-rc        = lv_rc.
    rs_out-transport = lv_cust.
    rs_out-data      = |proto={ lv_proto }|.
    rs_out-ok        = boolc( lv_rc = 0 ).
  ENDMETHOD.

ENDCLASS.
