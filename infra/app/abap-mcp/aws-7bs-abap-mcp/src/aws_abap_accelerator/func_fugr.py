# -*- coding: utf-8 -*-
"""Genera (headless) los archivos abapGit de un GRUPO DE FUNCIONES (FUGR) con sus
FUNCTION MODULES, parametrizado. La unidad de abapGit es el FUGR: los FM viven
dentro. Salida = {filename: content} para pasar a /crear_objeto object_type=FUGR.

Formato verificado por serialize real de un FUGR en TAILOY (RSIMP/RSEXP/RSCHA/
RSTBL/RSEXC; fuente por FM en <grp>.fugr.<fm>.abap; includes TOP + SAPL; main xml).

Contrato de cada FM (dict):
  name        : nombre del FM (str)
  short_text  : texto breve (opcional)
  source      : cuerpo ABAP entre FUNCTION/ENDFUNCTION (str)
  params      : { importing:[...], exporting:[...], changing:[...],
                  tables:[...], exceptions:[str,...] }   (todo opcional)
    importing/exporting/changing item: { name, type? , dbfield? , default? ,
                                         optional? , reference? }
    tables item:                       { name, dbstruct? , type? , optional? }
"""
BOM = "﻿"


def _esc(v):
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _progdir(name, subc):
    return (BOM + '<?xml version="1.0" encoding="utf-8"?>\n<abapGit version="v1.0.0">\n'
            ' <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n'
            '   <PROGDIR>\n    <NAME>' + name + '</NAME>\n    <DBAPL>S</DBAPL>\n    <DBNA>D$</DBNA>\n'
            '    <SUBC>' + subc + '</SUBC>\n    <APPL>S</APPL>\n    <FIXPT>X</FIXPT>\n'
            '    <UCCHECK>X</UCCHECK>\n   </PROGDIR>\n  </asx:values>\n </asx:abap>\n</abapGit>\n')


def _scalar(tag, x):
    """RSIMP/RSEXP/RSCHA con tipo (TYP) o referencia a campo DDIC (DBFIELD)."""
    s = "      <" + tag + ">\n       <PARAMETER>" + _esc(x["name"].upper()) + "</PARAMETER>\n"
    if x.get("type"):
        s += "       <TYP>" + _esc(x["type"]) + "</TYP>\n"
    elif x.get("dbfield"):
        s += "       <DBFIELD>" + _esc(x["dbfield"].upper()) + "</DBFIELD>\n"
    if x.get("default") is not None:
        s += "       <DEFAULT>" + _esc(x["default"]) + "</DEFAULT>\n"
    if x.get("reference"):
        s += "       <REFERENCE>X</REFERENCE>\n"
    if x.get("optional"):
        s += "       <OPTIONAL>X</OPTIONAL>\n"
    s += "      </" + tag + ">\n"
    return s


def _params_xml(fm):
    p = fm.get("params") or {}
    out = ""
    imp = p.get("importing") or []
    if imp:
        out += "     <IMPORT>\n" + "".join(_scalar("RSIMP", x) for x in imp) + "     </IMPORT>\n"
    exp = p.get("exporting") or []
    if exp:
        out += "     <EXPORT>\n" + "".join(_scalar("RSEXP", x) for x in exp) + "     </EXPORT>\n"
    cha = p.get("changing") or []
    if cha:
        out += "     <CHANGING>\n" + "".join(_scalar("RSCHA", x) for x in cha) + "     </CHANGING>\n"
    tbl = p.get("tables") or []
    if tbl:
        out += "     <TABLES>\n"
        for x in tbl:
            out += "      <RSTBL>\n       <PARAMETER>" + _esc(x["name"].upper()) + "</PARAMETER>\n"
            if x.get("dbstruct"):
                out += "       <DBSTRUCT>" + _esc(x["dbstruct"].upper()) + "</DBSTRUCT>\n"
            elif x.get("type"):
                out += "       <TYP>" + _esc(x["type"]) + "</TYP>\n"
            if x.get("optional"):
                out += "       <OPTIONAL>X</OPTIONAL>\n"
            out += "      </RSTBL>\n"
        out += "     </TABLES>\n"
    exc = p.get("exceptions") or []
    if exc:
        out += "     <EXCEPTION>\n"
        for e in exc:
            out += "      <RSEXC>\n       <EXCEPTION>" + _esc(e.upper()) + "</EXCEPTION>\n      </RSEXC>\n"
        out += "     </EXCEPTION>\n"
    return out


def build_fugr(group, fms, descripcion=""):
    """Devuelve {filename: content} con el bundle abapGit del FUGR + sus FM."""
    G = group.upper()
    g = G.lower()
    pre = g + ".fugr."
    files = {}

    # Fuente por FM: <grp>.fugr.<fm>.abap  (la firma va en el XML, no en la fuente)
    for fm in fms:
        fn = fm["name"].upper()
        body = (fm.get("source") or "").rstrip("\n")
        files[pre + fn.lower() + ".abap"] = "FUNCTION " + fn + ".\n" + body + "\nENDFUNCTION.\n"

    # Include TOP (FUNCTION-POOL) y programa principal SAPL
    files[pre + "l" + g + "top.xml"] = _progdir("L" + G + "TOP", "I")
    files[pre + "l" + g + "top.abap"] = "FUNCTION-POOL " + G + ".\n"
    files[pre + "sapl" + g + ".xml"] = _progdir("SAPL" + G, "F")
    files[pre + "sapl" + g + ".abap"] = (
        "  INCLUDE L" + G + "TOP.                        \" Global Data\n"
        "  INCLUDE L" + G + "UXX.                        \" Function Modules\n")

    # Main FUGR xml: AREAT + INCLUDES + FUNCTIONS
    funcs = ""
    for fm in fms:
        fn = fm["name"].upper()
        funcs += ("    <item>\n     <FUNCNAME>" + _esc(fn) + "</FUNCNAME>\n"
                  "     <SHORT_TEXT>" + _esc(fm.get("short_text") or fn) + "</SHORT_TEXT>\n"
                  + _params_xml(fm) + "    </item>\n")
    files[pre + "xml"] = (
        BOM + '<?xml version="1.0" encoding="utf-8"?>\n'
        '<abapGit version="v1.0.0" serializer="LCL_OBJECT_FUGR" serializer_version="v1.0.0">\n'
        ' <asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0">\n  <asx:values>\n'
        '   <AREAT>' + _esc(descripcion or G) + '</AREAT>\n'
        '   <INCLUDES>\n    <SOBJ_NAME>L' + G + 'TOP</SOBJ_NAME>\n'
        '    <SOBJ_NAME>SAPL' + G + '</SOBJ_NAME>\n   </INCLUDES>\n'
        '   <FUNCTIONS>\n' + funcs + '   </FUNCTIONS>\n'
        '  </asx:values>\n </asx:abap>\n</abapGit>\n')
    return files
