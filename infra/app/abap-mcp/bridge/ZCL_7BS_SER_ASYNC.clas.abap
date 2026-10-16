CLASS zcl_7bs_ser_async DEFINITION
  PUBLIC
  CREATE PUBLIC.

  PUBLIC SECTION.
    TYPES: BEGIN OF ty_hdr,
             status  TYPE c LENGTH 1,   " R=running D=done E=error
             total   TYPE i,
             done    TYPE i,
             size    TYPE i,
             nchunks TYPE i,
             errtxt  TYPE string,
             package TYPE devclass,
             uname   TYPE syuname,
           END OF ty_hdr.
    TYPES: BEGIN OF ty_obj,
             object   TYPE trobjtype,
             obj_name TYPE sobj_name,
           END OF ty_obj.
    TYPES ty_items TYPE STANDARD TABLE OF ty_obj WITH DEFAULT KEY.
    " Selector de que objetos serializar: paquete(+subpkg) / mascara de nombre /
    " orden de transporte / lista explicita / filtro por tipo (incluir/excluir CSV).
    TYPES: BEGIN OF ty_spec,
             package   TYPE devclass,
             subpkg    TYPE abap_bool,
             mask      TYPE string,
             transport TYPE trkorr,
             types_in  TYPE string,
             types_ex  TYPE string,
             items     TYPE ty_items,
           END OF ty_spec.
    CONSTANTS c_chunk TYPE i VALUE 2097152.   " 2 MB por chunk (fetch acotado)

    CLASS-METHODS start
      IMPORTING is_spec TYPE ty_spec
      RETURNING VALUE(rv_jobid) TYPE string
      RAISING   zcx_abapgit_exception.
    CLASS-METHODS run
      IMPORTING iv_jobid TYPE string.
    CLASS-METHODS status
      IMPORTING iv_jobid TYPE string
      RETURNING VALUE(rs_hdr) TYPE ty_hdr.
    CLASS-METHODS chunk
      IMPORTING iv_jobid TYPE string
                iv_index TYPE i
      RETURNING VALUE(rv_data) TYPE xstring.

  PRIVATE SECTION.
    CLASS-METHODS hdr_id   IMPORTING iv_jobid TYPE string RETURNING VALUE(rv) TYPE indx-srtfd.
    CLASS-METHODS spec_id  IMPORTING iv_jobid TYPE string RETURNING VALUE(rv) TYPE indx-srtfd.
    CLASS-METHODS chunk_id IMPORTING iv_jobid TYPE string iv_index TYPE i RETURNING VALUE(rv) TYPE indx-srtfd.
    CLASS-METHODS save_hdr IMPORTING iv_jobid TYPE string is_hdr TYPE ty_hdr.
    CLASS-METHODS build_list IMPORTING is_spec TYPE ty_spec RETURNING VALUE(rt) TYPE ty_items.
ENDCLASS.


CLASS zcl_7bs_ser_async IMPLEMENTATION.

  METHOD hdr_id.
    rv = |{ iv_jobid }H|.
  ENDMETHOD.

  METHOD spec_id.
    rv = |{ iv_jobid }S|.
  ENDMETHOD.

  METHOD chunk_id.
    rv = |{ iv_jobid }C{ iv_index WIDTH = 5 ALIGN = RIGHT PAD = '0' }|.
  ENDMETHOD.

  METHOD save_hdr.
    DATA ls_wa TYPE indx.
    DATA(lv_id) = hdr_id( iv_jobid ).
    EXPORT hdr = is_hdr TO DATABASE indx(z7) FROM ls_wa ID lv_id.
    COMMIT WORK.
  ENDMETHOD.

  METHOD build_list.
    " Fuente: lista explicita > orden de transporte > paquete. Luego filtros mask + tipo.
    IF is_spec-items IS NOT INITIAL.
      rt = is_spec-items.
    ELSEIF is_spec-transport IS NOT INITIAL.
      DATA lr_tr TYPE RANGE OF trkorr.
      lr_tr = VALUE #( ( sign = 'I' option = 'EQ' low = is_spec-transport ) ).
      SELECT trkorr FROM e070 WHERE strkorr = @is_spec-transport INTO TABLE @DATA(lt_task).
      LOOP AT lt_task INTO DATA(ls_task).
        lr_tr = VALUE #( BASE lr_tr ( sign = 'I' option = 'EQ' low = ls_task-trkorr ) ).
      ENDLOOP.
      SELECT DISTINCT object, obj_name FROM e071
        WHERE trkorr IN @lr_tr AND pgmid = 'R3TR'
          AND object <> @space AND obj_name <> @space
        INTO TABLE @DATA(lt_e071).
      LOOP AT lt_e071 INTO DATA(ls_e).
        APPEND VALUE ty_obj( object = ls_e-object obj_name = ls_e-obj_name ) TO rt.
      ENDLOOP.
    ELSE.
      DATA(lt_tadir) = zcl_abapgit_factory=>get_tadir( )->read(
        iv_package            = is_spec-package
        iv_ignore_subpackages = xsdbool( is_spec-subpkg = abap_false ) ).
      LOOP AT lt_tadir INTO DATA(ls_t).
        APPEND VALUE ty_obj( object = ls_t-object obj_name = ls_t-obj_name ) TO rt.
      ENDLOOP.
    ENDIF.

    IF is_spec-mask IS NOT INITIAL.
      DATA(lv_mask) = to_upper( is_spec-mask ).
      DELETE rt WHERE obj_name NP lv_mask.
    ENDIF.

    DATA lr_type TYPE RANGE OF trobjtype.
    IF is_spec-types_in IS NOT INITIAL.
      SPLIT is_spec-types_in AT ',' INTO TABLE DATA(lt_ti).
      LOOP AT lt_ti INTO DATA(lv_ti).
        DATA(lv_t) = to_upper( condense( lv_ti ) ).
        IF lv_t IS NOT INITIAL.
          lr_type = VALUE #( BASE lr_type ( sign = 'I' option = 'EQ' low = lv_t ) ).
        ENDIF.
      ENDLOOP.
      IF lr_type IS NOT INITIAL.
        DELETE rt WHERE object NOT IN lr_type.
      ENDIF.
    ENDIF.
    IF is_spec-types_ex IS NOT INITIAL.
      DATA lr_ex TYPE RANGE OF trobjtype.
      SPLIT is_spec-types_ex AT ',' INTO TABLE DATA(lt_te).
      LOOP AT lt_te INTO DATA(lv_te).
        DATA(lv_e) = to_upper( condense( lv_te ) ).
        IF lv_e IS NOT INITIAL.
          lr_ex = VALUE #( BASE lr_ex ( sign = 'I' option = 'EQ' low = lv_e ) ).
        ENDIF.
      ENDLOOP.
      IF lr_ex IS NOT INITIAL.
        DELETE rt WHERE object IN lr_ex.
      ENDIF.
    ENDIF.
  ENDMETHOD.

  METHOD start.
    DATA: lv_ts   TYPE timestamp,
          lv_jobc TYPE btcjob,
          lv_jobn TYPE tbtcjob-jobcount,
          ls_wa   TYPE indx.
    GET TIME STAMP FIELD lv_ts.
    rv_jobid = |{ lv_ts }|.
    DATA(lv_rnd) = cl_abap_random_int=>create(
      seed = CONV i( sy-uzeit ) min = 10 max = 99 )->get_next( ).
    rv_jobid = |{ rv_jobid }{ lv_rnd }|.

    DATA(lv_sid) = spec_id( rv_jobid ).
    EXPORT spec = is_spec TO DATABASE indx(z7) FROM ls_wa ID lv_sid.
    COMMIT WORK.

    save_hdr( iv_jobid = rv_jobid
              is_hdr   = VALUE ty_hdr( status = 'R' package = is_spec-package uname = sy-uname ) ).

    lv_jobc = |ZSER_{ rv_jobid }|.
    CALL FUNCTION 'JOB_OPEN'
      EXPORTING jobname = lv_jobc
      IMPORTING jobcount = lv_jobn
      EXCEPTIONS OTHERS = 1.
    IF sy-subrc <> 0.
      zcx_abapgit_exception=>raise( |JOB_OPEN falló subrc { sy-subrc }| ).
    ENDIF.
    SUBMIT zbs_7bs_ser_pkg WITH p_jobid = rv_jobid VIA JOB lv_jobc NUMBER lv_jobn AND RETURN.
    CALL FUNCTION 'JOB_CLOSE'
      EXPORTING jobcount = lv_jobn jobname = lv_jobc strtimmed = abap_true
      EXCEPTIONS OTHERS = 1.
    IF sy-subrc <> 0.
      zcx_abapgit_exception=>raise( |JOB_CLOSE falló subrc { sy-subrc }| ).
    ENDIF.
  ENDMETHOD.

  METHOD run.
    DATA: ls_hdr  TYPE ty_hdr,
          ls_spec TYPE ty_spec,
          ls_item TYPE zif_abapgit_definitions=>ty_item,
          lv_class TYPE string,
          li_obj  TYPE REF TO zif_abapgit_object,
          ls_wa   TYPE indx.
    ls_hdr-status = 'R'. ls_hdr-uname = sy-uname.
    TRY.
        DATA(lv_sid) = spec_id( iv_jobid ).
        IMPORT spec = ls_spec FROM DATABASE indx(z7) TO ls_wa ID lv_sid.
        ls_hdr-package = ls_spec-package.
        DATA(lt_items) = build_list( ls_spec ).
        ls_hdr-total = lines( lt_items ).
        DATA(lo_zip) = NEW cl_abap_zip( ).
        LOOP AT lt_items INTO DATA(ls_o).
          TRY.
              CLEAR ls_item.
              ls_item-obj_type = ls_o-object.
              ls_item-obj_name = ls_o-obj_name.
              DATA(lo_files) = zcl_abapgit_objects_files=>new( ls_item ).
              lv_class = |ZCL_ABAPGIT_OBJECT_{ ls_o-object }|.
              CREATE OBJECT li_obj TYPE (lv_class)
                EXPORTING is_item = ls_item iv_language = sy-langu io_files = lo_files.
              DATA(lo_xml) = NEW zcl_abapgit_xml_output( ).
              li_obj->serialize( lo_xml ).
              lo_files->add_xml( ii_xml = lo_xml is_metadata = li_obj->get_metadata( ) ).
              LOOP AT lo_files->get_files( ) INTO DATA(ls_f).
                lo_zip->add( name = ls_f-filename content = ls_f-data ).
              ENDLOOP.
              ls_hdr-done = ls_hdr-done + 1.
            CATCH cx_root.
              CONTINUE.
          ENDTRY.
          IF ls_hdr-done MOD 50 = 0.
            save_hdr( iv_jobid = iv_jobid is_hdr = ls_hdr ).
          ENDIF.
        ENDLOOP.
        DATA(lv_zip) = lo_zip->save( ).
        ls_hdr-size = xstrlen( lv_zip ).
        DATA: lv_off TYPE i, lv_len TYPE i, lv_seq TYPE i.
        lv_off = 0. lv_seq = 0.
        WHILE lv_off < ls_hdr-size.
          lv_len = ls_hdr-size - lv_off.
          IF lv_len > c_chunk. lv_len = c_chunk. ENDIF.
          lv_seq = lv_seq + 1.
          DATA(lv_piece) = lv_zip+lv_off(lv_len).
          DATA(lv_cid) = chunk_id( iv_jobid = iv_jobid iv_index = lv_seq ).
          EXPORT chunk = lv_piece TO DATABASE indx(z7) FROM ls_wa ID lv_cid.
          lv_off = lv_off + lv_len.
        ENDWHILE.
        ls_hdr-nchunks = lv_seq.
        ls_hdr-status = 'D'.
        save_hdr( iv_jobid = iv_jobid is_hdr = ls_hdr ).
      CATCH cx_root INTO DATA(lx).
        ls_hdr-status = 'E'. ls_hdr-errtxt = lx->get_text( ).
        save_hdr( iv_jobid = iv_jobid is_hdr = ls_hdr ).
    ENDTRY.
  ENDMETHOD.

  METHOD status.
    DATA ls_wa TYPE indx.
    DATA(lv_id) = hdr_id( iv_jobid ).
    IMPORT hdr = rs_hdr FROM DATABASE indx(z7) TO ls_wa ID lv_id.
    IF sy-subrc <> 0.
      CLEAR rs_hdr.
    ENDIF.
  ENDMETHOD.

  METHOD chunk.
    DATA ls_wa TYPE indx.
    DATA(lv_id) = chunk_id( iv_jobid = iv_jobid iv_index = iv_index ).
    IMPORT chunk = rv_data FROM DATABASE indx(z7) TO ls_wa ID lv_id.
    IF sy-subrc <> 0.
      CLEAR rv_data.
    ENDIF.
  ENDMETHOD.

ENDCLASS.
