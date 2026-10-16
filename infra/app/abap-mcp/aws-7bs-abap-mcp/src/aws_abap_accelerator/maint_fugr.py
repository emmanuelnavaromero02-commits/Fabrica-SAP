# -*- coding: utf-8 -*-
"""Genera (headless) los archivos abapGit del grupo de funciones de mantenimiento SM30
de UN paso para una tabla transparente, parametrizado por tabla + campos. Es lo mismo
que produce SE54/TABLE_MAINTENANCE_GENERATOR (que es GUI-only), pero como archivos que
el bridge abapGit deserializa+activa. El registro SM30 (TVDIR) se hace aparte (/registrar_sm30).

Validado byte-idéntico contra la salida real de SE54 (ZOSMM_FAMILIA) salvo metadata variable.
"""
import os

BOM = "﻿"

# Bloque <FUNCTIONS> fijo (firmas estándar del framework SVIM), con el nombre de tabla
# como marcador ZOSMM_FAMILIA → se sustituye por la tabla destino en build_maint_fugr.
with open(os.path.join(os.path.dirname(__file__), "maint_funcs.xml"), "r", encoding="utf-8") as _fh:
    FUNCTIONS_BLOCK = _fh.read().rstrip("\n") + "\n"

# DATATYPE (DDIC) -> FORMAT de dynpro. Con FROM_DICT=X el atributo real lo toma del
# diccionario; este mapeo es el hint.
FMT = {"CHAR": "CHAR", "CUKY": "CHAR", "UNIT": "CHAR", "LANG": "CHAR", "NUMC": "NUMC",
       "DATS": "DATE", "TIMS": "TIME", "DEC": "DEC", "CURR": "DEC", "QUAN": "DEC"}


def _u(n):
    return "_" * int(n)


def _g(d, k):
    return d.get(k) or d.get(k.upper()) or d.get(k.lower())


def build_maint_fugr(tab, fields, fgrp=None):
    """Devuelve {filename: content} con los 14 archivos abapGit del FUGR de mantenimiento.

    tab    : nombre de la tabla (str)
    fields : lista de dicts estilo DFIES (fieldname, keyflag, outputlen, datatype) — p.ej.
             la salida de /campos_tabla. Los campos de cliente (DATATYPE=CLNT) se omiten.
    fgrp   : grupo de funciones; por defecto = nombre de la tabla (como SE54).
    """
    T = tab.upper(); F = (fgrp or tab).upper(); t = T.lower(); f = F.lower()
    cols = [x for x in fields if _g(x, "datatype") != "CLNT"]
    files = {}
    # Los archivos abapGit se nombran por el objeto FUGR (grupo de funciones), no por
    # la tabla. Con function_group != tabla el prefijo debe usar 'f' (FUGR), si no
    # abapGit busca <fugr>.fugr.xml y no lo encuentra (estaba como <tabla>.fugr.xml).
    pre = f + ".fugr."

    files[pre + "tableframe_" + t + ".abap"] = (
        "*---------------------------------------------------------------------*\n"
        "*    program for:   TABLEFRAME_" + T + "\n"
        "*---------------------------------------------------------------------*\n"
        "FUNCTION TABLEFRAME_" + T + "      .\n\n"
        "  PERFORM TABLEFRAME TABLES X_HEADER X_NAMTAB DBA_SELLIST DPL_SELLIST\n"
        "                            EXCL_CUA_FUNCT\n"
        "                     USING  CORR_NUMBER VIEW_ACTION VIEW_NAME.\n\n"
        "ENDFUNCTION.\n")
    files[pre + "tableproc_" + t + ".abap"] = (
        "*---------------------------------------------------------------------*\n"
        "*    program for:   TABLEPROC_" + T + "\n"
        "*---------------------------------------------------------------------*\n"
        "FUNCTION TABLEPROC_" + T + "       .\n\n  PERFORM TABLEPROC.\n\nENDFUNCTION.\n")

    def progdir(name, sqlx=True, subc="I"):
        s = BOM + '<?xml version="1.0" encoding="utf-8"?>\n<abapGit version="v1.0.0">\n <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n   <PROGDIR>\n    <NAME>' + name + '</NAME>\n'
        if sqlx:
            s += '    <SQLX>X</SQLX>\n'
        s += '    <DBAPL>S</DBAPL>\n    <DBNA>D$</DBNA>\n    <SUBC>' + subc + '</SUBC>\n    <APPL>S</APPL>\n'
        s += '    <RLOAD>S</RLOAD>\n    <FIXPT>X</FIXPT>\n    <LDBNAME>D$S</LDBNAME>\n    <UCCHECK>X</UCCHECK>\n   </PROGDIR>\n  </asx:values>\n </asx:abap>\n</abapGit>\n'
        return s

    files[pre + "l" + f + "f00.xml"] = progdir("L" + F + "F00")
    files[pre + "l" + f + "f00.abap"] = ("*---------------------------------------------------------------------*\n*    view related FORM routines\n*---------------------------------------------------------------------*\n\n* base table related FORM-routines.............\nINCLUDE LSVIMFTX .\n")
    files[pre + "l" + f + "i00.xml"] = progdir("L" + F + "I00")
    files[pre + "l" + f + "i00.abap"] = ("*---------------------------------------------------------------------*\n*    view related PAI modules\n*---------------------------------------------------------------------*\n\nINCLUDE LSVIMITX . \"base table related PAI modules\n")

    t00xml = BOM + '<?xml version="1.0" encoding="utf-8"?>\n<abapGit version="v1.0.0">\n <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n   <PROGDIR>\n    <NAME>L' + F + 'T00</NAME>\n    <DBAPL>S</DBAPL>\n    <DBNA>D$</DBNA>\n    <SUBC>I</SUBC>\n    <APPL>S</APPL>\n    <RLOAD>S</RLOAD>\n    <FIXPT>X</FIXPT>\n    <LDBNAME>D$S</LDBNAME>\n    <UCCHECK>X</UCCHECK>\n   </PROGDIR>\n  </asx:values>\n </asx:abap>\n</abapGit>\n'
    files[pre + "l" + f + "t00.xml"] = t00xml
    files[pre + "l" + f + "t00.abap"] = (
        "*---------------------------------------------------------------------*\n*    view related data declarations\n*---------------------------------------------------------------------*\n"
        "*...processing: " + T + "...................................*\n"
        "DATA:  BEGIN OF STATUS_" + T + "                 .   \"state vector\n"
        "         INCLUDE STRUCTURE VIMSTATUS.\n"
        "DATA:  END OF STATUS_" + T + "                 .\n"
        "CONTROLS: TCTRL_" + T + "\n            TYPE TABLEVIEW USING SCREEN '0001'.\n"
        "*.........table declarations:.................................*\n"
        "TABLES: *" + T + "                 .\n"
        "TABLES: " + T + "                  .\n\n"
        "* general table data declarations..............\n  INCLUDE LSVIMTDT                                .\n")

    topxml = BOM + '<?xml version="1.0" encoding="utf-8"?>\n<abapGit version="v1.0.0">\n <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n   <PROGDIR>\n    <NAME>L' + F + 'TOP</NAME>\n    <DBAPL>S</DBAPL>\n    <DBNA>D$</DBNA>\n    <SUBC>I</SUBC>\n    <APPL>S</APPL>\n    <FIXPT>X</FIXPT>\n    <LDBNAME>D$S</LDBNAME>\n    <UCCHECK>X</UCCHECK>\n   </PROGDIR>\n  </asx:values>\n </asx:abap>\n</abapGit>\n'
    files[pre + "l" + f + "top.xml"] = topxml
    files[pre + "l" + f + "top.abap"] = (
        "FUNCTION-POOL " + F + "              MESSAGE-ID SV.\n\n"
        "* INCLUDE L" + F + "D...                 \" Local class definition\n"
        "  INCLUDE LSVIMDAT                                . \"general data decl.\n"
        "  INCLUDE L" + F + "T00                       . \"view rel. data dcl.\n")

    files[pre + "sapl" + f + ".xml"] = progdir("SAPL" + F, sqlx=True, subc="F")
    files[pre + "sapl" + f + ".abap"] = (
        "*******************************************************************\n*   System-defined Include-files.                                 *\n*******************************************************************\n"
        "  INCLUDE L" + F + "TOP.                 \" Global Declarations\n"
        "  INCLUDE L" + F + "UXX.                 \" Function Modules\n\n"
        "*******************************************************************\n*   User-defined Include-files (if necessary).                    *\n*******************************************************************\n"
        "* INCLUDE L" + F + "F...                 \" Subroutines\n"
        "* INCLUDE L" + F + "O...                 \" PBO-Modules\n"
        "* INCLUDE L" + F + "I...                 \" PAI-Modules\n"
        "* INCLUDE L" + F + "E...                 \" Events\n"
        "* INCLUDE L" + F + "P...                 \" Local class implement.\n"
        "* INCLUDE L" + F + "T99.                 \" ABAP Unit tests\n"
        "  INCLUDE L" + F + "F00                       . \" subprograms\n"
        "  INCLUDE L" + F + "I00                       . \" PAI modules\n"
        "  INCLUDE LSVIMFXX                                . \" subprograms\n"
        "  INCLUDE LSVIMOXX                                . \" PBO modules\n"
        "  INCLUDE LSVIMIXX                                . \" PAI modules\n")

    chain_all = "".join("    FIELD " + T + "-" + _g(c, "fieldname") + " .\n" for c in cols)
    keys = [c for c in cols if _g(c, "keyflag") == "X"]
    chain_key = "".join("    FIELD " + T + "-" + _g(c, "fieldname") + " .\n" for c in keys)
    files[pre + "screen_0001.abap"] = (
        "PROCESS BEFORE OUTPUT.\n MODULE LISTE_INITIALISIEREN.\n LOOP AT EXTRACT WITH CONTROL\n"
        "  TCTRL_" + T + " CURSOR NEXTLINE.\n   MODULE LISTE_SHOW_LISTE.\n ENDLOOP.\n*\n"
        "PROCESS AFTER INPUT.\n MODULE LISTE_EXIT_COMMAND AT EXIT-COMMAND.\n MODULE LISTE_BEFORE_LOOP.\n LOOP AT EXTRACT.\n   MODULE LISTE_INIT_WORKAREA.\n   CHAIN.\n"
        + chain_all +
        "    MODULE SET_UPDATE_FLAG ON CHAIN-REQUEST.\n   ENDCHAIN.\n   FIELD VIM_MARKED MODULE LISTE_MARK_CHECKBOX.\n   CHAIN.\n"
        + chain_key +
        "    MODULE LISTE_UPDATE_LISTE.\n   ENDCHAIN.\n ENDLOOP.\n MODULE LISTE_AFTER_LOOP.\n")

    heads = ""
    for i, c in enumerate(cols, 1):
        fn = _g(c, "fieldname"); ol = int(_g(c, "outputlen") or 10)
        heads += ("      <RPY_DYFATC>\n"
                  "       <CONT_TYPE>TABLE_CTRL</CONT_TYPE>\n       <CONT_NAME>TCTRL_" + T + "</CONT_NAME>\n"
                  "       <TYPE>TEXT</TYPE>\n       <NAME>*" + T + "-" + fn + "</NAME>\n"
                  "       <LINE>001</LINE>\n       <COLUMN>%03d</COLUMN>\n       <LENGTH>040</LENGTH>\n       <VISLENGTH>%03d</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n" % (i, ol)
                  + "       <FORMAT>CHAR</FORMAT>\n       <FROM_DICT>X</FROM_DICT>\n       <MODIFIC>V</MODIFIC>\n       <REQU_ENTRY>N</REQU_ENTRY>\n       <TC_HEADING>X</TC_HEADING>\n      </RPY_DYFATC>\n")

    tmpls = ""
    for i, c in enumerate(cols, 1):
        fn = _g(c, "fieldname"); ol = int(_g(c, "outputlen") or 10); iskey = (_g(c, "keyflag") == "X")
        fmt = FMT.get(_g(c, "datatype"), "CHAR")
        s = ("      <RPY_DYFATC>\n"
             "       <CONT_TYPE>TABLE_CTRL</CONT_TYPE>\n       <CONT_NAME>TCTRL_" + T + "</CONT_NAME>\n"
             "       <TYPE>TEMPLATE</TYPE>\n       <NAME>" + T + "-" + fn + "</NAME>\n")
        if iskey:
            s += "       <TEXT>" + _u(ol) + "</TEXT>\n"
        s += ("       <LINE>001</LINE>\n       <COLUMN>%03d</COLUMN>\n       <LENGTH>%03d</LENGTH>\n       <VISLENGTH>%03d</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n" % (i, ol, ol))
        if iskey:
            s += "       <GROUP1>KEY</GROUP1>\n"
        s += "       <FORMAT>" + fmt + "</FORMAT>\n       <FROM_DICT>X</FROM_DICT>\n"
        if iskey:
            s += "       <MODIFIC>X</MODIFIC>\n"
        s += "       <UP_LOWER>X</UP_LOWER>\n"
        if iskey:
            s += "       <OUTPUT_FLD>X</OUTPUT_FLD>\n       <REQU_ENTRY>N</REQU_ENTRY>\n"
        else:
            s += "       <INPUT_FLD>X</INPUT_FLD>\n       <OUTPUT_FLD>X</OUTPUT_FLD>\n"
        s += "      </RPY_DYFATC>\n"
        tmpls += s

    incl = "".join("    <SOBJ_NAME>L" + F + x + "</SOBJ_NAME>\n" for x in ("F00", "I00", "T00", "TOP")) + "    <SOBJ_NAME>SAPL" + F + "</SOBJ_NAME>\n"
    funcs = FUNCTIONS_BLOCK.replace("ZOSMM_FAMILIA", T)
    fugr = (BOM + '<?xml version="1.0" encoding="utf-8"?>\n'
            '<abapGit version="v1.0.0" serializer="LCL_OBJECT_FUGR" serializer_version="v1.0.0">\n'
            ' <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n'
            '   <AREAT>Actualiz.tablas ampliada (generada)</AREAT>\n   <INCLUDES>\n' + incl + '   </INCLUDES>\n'
            + funcs +
            '   <DYNPROS>\n    <item>\n     <HEADER>\n'
            '      <PROGRAM>SAPL' + F + '</PROGRAM>\n      <SCREEN>0001</SCREEN>\n      <LANGUAGE>S</LANGUAGE>\n'
            '      <DESCRIPT>Act.vistas: Imagen resumen ' + T + '</DESCRIPT>\n      <TYPE>N</TYPE>\n      <NEXTSCREEN>0001</NEXTSCREEN>\n      <LINES>061</LINES>\n      <COLUMNS>255</COLUMNS>\n     </HEADER>\n'
            '     <CONTAINERS>\n      <RPY_DYCATT>\n       <TYPE>SCREEN</TYPE>\n       <NAME>SCREEN</NAME>\n      </RPY_DYCATT>\n'
            '      <RPY_DYCATT>\n       <TYPE>TABLE_CTRL</TYPE>\n       <NAME>TCTRL_' + T + '</NAME>\n       <ELEMENT_OF>SCREEN</ELEMENT_OF>\n'
            '       <LINE>001</LINE>\n       <COLUMN>001</COLUMN>\n       <LENGTH>255</LENGTH>\n       <HEIGHT>059</HEIGHT>\n'
            '       <C_RESIZE_V>X</C_RESIZE_V>\n       <C_RESIZE_H>X</C_RESIZE_H>\n       <C_SCROLL_V>X</C_SCROLL_V>\n       <C_SCROLL_H>X</C_SCROLL_H>\n'
            '       <TC_TABTYPE>ENTRY</TC_TABTYPE>\n       <TC_SEPAR_V>X</TC_SEPAR_V>\n       <TC_SEPAR_H>X</TC_SEPAR_H>\n       <TC_TITLE>X</TC_TITLE>\n       <TC_HEADER>X</TC_HEADER>\n       <TC_CONFIG>X</TC_CONFIG>\n       <TC_SEL_LNS>MULTIPLE</TC_SEL_LNS>\n       <TC_SEL_CLS>NONE</TC_SEL_CLS>\n       <TC_LSEL_CL>X</TC_LSEL_CL>\n       <TC_FIXCOL>001</TC_FIXCOL>\n      </RPY_DYCATT>\n     </CONTAINERS>\n'
            '     <FIELDS>\n'
            '      <RPY_DYFATC>\n       <CONT_TYPE>SCREEN</CONT_TYPE>\n       <CONT_NAME>SCREEN</CONT_NAME>\n       <TYPE>PUSH_TMPL</TYPE>\n       <NAME>VIM_POSI_PUSH</NAME>\n       <TEXT>____________________</TEXT>\n       <LINE>061</LINE>\n       <COLUMN>019</COLUMN>\n       <LENGTH>020</LENGTH>\n       <VISLENGTH>020</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n       <PUSH_FCODE>POSI</PUSH_FCODE>\n       <FORMAT>CHAR</FORMAT>\n       <OUTPUT_FLD>X</OUTPUT_FLD>\n       <REQU_ENTRY>N</REQU_ENTRY>\n      </RPY_DYFATC>\n'
            '      <RPY_DYFATC>\n       <CONT_TYPE>SCREEN</CONT_TYPE>\n       <CONT_NAME>SCREEN</CONT_NAME>\n       <TYPE>TEMPLATE</TYPE>\n       <NAME>VIM_POSITION_INFO</NAME>\n       <TEXT>______________________________</TEXT>\n       <LINE>061</LINE>\n       <COLUMN>040</COLUMN>\n       <LENGTH>030</LENGTH>\n       <VISLENGTH>030</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n       <FORMAT>CHAR</FORMAT>\n       <UP_LOWER>X</UP_LOWER>\n       <OUTPUT_FLD>X</OUTPUT_FLD>\n       <OUTPUTONLY>X</OUTPUTONLY>\n       <REQU_ENTRY>N</REQU_ENTRY>\n       <_--32_DIMENS>X</_--32_DIMENS>\n      </RPY_DYFATC>\n'
            '      <RPY_DYFATC>\n       <CONT_TYPE>SCREEN</CONT_TYPE>\n       <CONT_NAME>SCREEN</CONT_NAME>\n       <TYPE>OKCODE</TYPE>\n       <NAME>OK_CODE</NAME>\n       <TEXT>____________________</TEXT>\n       <LENGTH>020</LENGTH>\n       <VISLENGTH>020</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n       <FORMAT>CHAR</FORMAT>\n       <INPUT_FLD>X</INPUT_FLD>\n      </RPY_DYFATC>\n'
            '      <RPY_DYFATC>\n       <CONT_TYPE>TABLE_CTRL</CONT_TYPE>\n       <CONT_NAME>TCTRL_' + T + '</CONT_NAME>\n       <TYPE>TEMPLATE</TYPE>\n       <NAME>VIM_FRAME_FIELD</NAME>\n       <TEXT>____________________________________________________________</TEXT>\n       <LINE>001</LINE>\n       <LENGTH>060</LENGTH>\n       <VISLENGTH>060</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n       <FORMAT>CHAR</FORMAT>\n       <UP_LOWER>X</UP_LOWER>\n       <OUTPUT_FLD>X</OUTPUT_FLD>\n       <REQU_ENTRY>N</REQU_ENTRY>\n       <TC_TITLE>X</TC_TITLE>\n      </RPY_DYFATC>\n'
            + heads +
            '      <RPY_DYFATC>\n       <CONT_TYPE>TABLE_CTRL</CONT_TYPE>\n       <CONT_NAME>TCTRL_' + T + '</CONT_NAME>\n       <TYPE>CHECK</TYPE>\n       <NAME>VIM_MARKED</NAME>\n       <LINE>001</LINE>\n       <LENGTH>001</LENGTH>\n       <VISLENGTH>001</VISLENGTH>\n       <HEIGHT>001</HEIGHT>\n       <FORMAT>CHAR</FORMAT>\n       <INPUT_FLD>X</INPUT_FLD>\n       <OUTPUT_FLD>X</OUTPUT_FLD>\n       <TC_SELCOL>X</TC_SELCOL>\n      </RPY_DYFATC>\n'
            + tmpls +
            '     </FIELDS>\n    </item>\n   </DYNPROS>\n  </asx:values>\n </asx:abap>\n</abapGit>\n')
    files[pre + "xml"] = fugr
    return files
