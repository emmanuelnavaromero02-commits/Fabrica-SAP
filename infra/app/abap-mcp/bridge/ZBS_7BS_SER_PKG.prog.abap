REPORT zbs_7bs_ser_pkg.

" Worker en background del job de serializacion asincrona. El SELECTOR (paquete /
" mascara / orden / lista / tipos) viaja en un SPEC guardado en INDX(z7) por
" zcl_7bs_ser_async=>start; aqui solo se pasa el job_id. Corre en su propio work
" process (sin el limite de dialogo del ICF). Ver ZCL_7BS_SER_ASYNC.
PARAMETERS p_jobid TYPE char16.

START-OF-SELECTION.
  zcl_7bs_ser_async=>run( CONV #( p_jobid ) ).
