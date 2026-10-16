"""Framework HTML/CSS/JS mini estilo Fiori para los prototipos de la fabrica.

El agente NO escribe CSS ni JS — solo escribe el contenido del <body> usando
las clases `f-*` documentadas en `FRAMEWORK_PROMPT`. El activity envuelve
el output del agente con `wrap(body)` para producir el HTML self-contained.

CSS: ~6KB, JS: ~2KB. Patrones cubiertos: tabs, table editable, matchcode F4,
KPI cards, dialogs, badges, toolbar.
"""

_CSS = """\
:root{
  --b:#0a6ed1;--b2:#084788;--bg:#fafafa;--sf:#fff;--bo:#e0e0e0;
  --t:#333;--tm:#666;--ok:#107e3e;--wn:#df6e0c;--er:#bb0000;
  --r:4px;--g:8px;
  font-family:"72","Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-size:14px;line-height:1.5}
.f-header{background:var(--sf);border-bottom:1px solid var(--bo);padding:12px 24px;display:flex;align-items:center;gap:16px}
.f-header h1{font-size:16px;font-weight:600}
.f-spacer{flex:1}
.f-tabs{display:flex;background:var(--sf);border-bottom:1px solid var(--bo)}
.f-tab{padding:12px 20px;cursor:pointer;border-bottom:3px solid transparent;color:var(--tm);font-weight:500;user-select:none}
.f-tab:hover{background:#f5f8fc}
.f-tab.active{border-bottom-color:var(--b);color:var(--b)}
.f-main{padding:16px 24px}
.f-card{background:var(--sf);border:1px solid var(--bo);border-radius:var(--r);padding:16px;margin-bottom:16px}
.f-card h2{font-size:16px;font-weight:600;margin-bottom:12px;color:var(--t)}
.f-card h3{font-size:13px;font-weight:600;color:var(--tm);text-transform:uppercase;letter-spacing:.5px;margin:8px 0}
.f-toolbar{display:flex;align-items:center;gap:12px;padding:8px 0;flex-wrap:wrap}
.f-btn{padding:6px 14px;border-radius:var(--r);border:1px solid var(--bo);background:var(--sf);cursor:pointer;font-size:13px;color:var(--t)}
.f-btn:hover{background:#f0f0f0}
.f-btn.primary{background:var(--b);color:#fff;border-color:var(--b)}
.f-btn.primary:hover{background:var(--b2)}
.f-btn.danger{color:var(--er)}
.f-btn.icon{padding:4px 8px}
.f-table{width:100%;border-collapse:collapse;background:var(--sf)}
.f-table th{text-align:left;padding:10px;background:#f5f5f5;border-bottom:1px solid var(--bo);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.3px}
.f-table td{padding:6px 10px;border-bottom:1px solid var(--bo);vertical-align:middle}
.f-table tr:hover td{background:#fafbff}
.f-table .ro{background:#f5f5f5;color:var(--tm)}
.f-input,.f-select{width:100%;padding:6px 8px;border:1px solid var(--bo);border-radius:var(--r);font-size:13px;background:#fff;font-family:inherit}
.f-input:focus,.f-select:focus{border-color:var(--b);outline:none}
.f-mc{display:flex;gap:0}
.f-mc .f-input{border-right:none;border-radius:var(--r) 0 0 var(--r)}
.f-mc .f-btn{border-radius:0 var(--r) var(--r) 0;border-left-color:var(--bo);padding:6px 10px}
.f-badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.f-badge.ok{background:#e8f5e9;color:var(--ok)}
.f-badge.warn{background:#fff3e0;color:var(--wn)}
.f-badge.error{background:#ffebee;color:var(--er)}
.f-dialog-bg{position:fixed;inset:0;background:rgba(0,0,0,.3);display:none;align-items:center;justify-content:center;z-index:100}
.f-dialog-bg.open{display:flex}
.f-dialog{background:#fff;border-radius:var(--r);width:600px;max-width:90vw;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 4px 24px rgba(0,0,0,.2)}
.f-dialog-h{padding:14px 20px;border-bottom:1px solid var(--bo);font-weight:600;display:flex;align-items:center}
.f-dialog-b{padding:16px 20px;overflow:auto;flex:1}
.f-dialog-f{padding:12px 20px;border-top:1px solid var(--bo);text-align:right}
.f-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.f-kpi{background:var(--sf);border:1px solid var(--bo);border-radius:var(--r);padding:14px}
.f-kpi .lbl{font-size:11px;color:var(--tm);text-transform:uppercase;letter-spacing:.5px}
.f-kpi .val{font-size:24px;font-weight:600;color:var(--b);margin-top:4px}
.f-form{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.f-form label{font-size:12px;color:var(--tm);font-weight:600;display:block;margin-bottom:4px}
.f-hint{font-size:12px;color:var(--tm);margin-top:4px}
.f-row-actions{display:flex;gap:4px;justify-content:flex-end}
.view-hidden{display:none!important}
"""

_JS = """\
document.addEventListener('click',function(e){
  var tab=e.target.closest('.f-tab');
  if(tab){
    var v=tab.dataset.view;
    document.querySelectorAll('.f-tab').forEach(function(t){t.classList.toggle('active',t===tab)});
    document.querySelectorAll('[data-view-content]').forEach(function(s){
      s.classList.toggle('view-hidden',s.dataset.viewContent!==v);
    });
  }
  var mc=e.target.closest('[data-matchcode]');
  if(mc){
    var id=mc.dataset.matchcode;
    var dlg=document.getElementById(id);
    if(dlg)dlg.classList.add('open');
  }
  if(e.target.matches('.f-dialog-bg')||e.target.closest('[data-close]')){
    var bg=e.target.closest('.f-dialog-bg');
    if(bg)bg.classList.remove('open');
  }
  var sel=e.target.closest('[data-mc-select]');
  if(sel){
    var val=sel.dataset.mcSelect;
    var dlg2=sel.closest('.f-dialog-bg');
    var tgt=dlg2&&dlg2.dataset.target?document.querySelector(dlg2.dataset.target):null;
    if(tgt)tgt.value=val;
    if(dlg2)dlg2.classList.remove('open');
  }
  var add=e.target.closest('[data-add-row]');
  if(add){
    var tid=add.dataset.addRow;
    var tbl=document.getElementById(tid);
    if(tbl&&tbl.tBodies[0]&&tbl.tBodies[0].rows[0]){
      var nr=tbl.tBodies[0].rows[0].cloneNode(true);
      nr.querySelectorAll('input,select').forEach(function(i){i.value=''});
      tbl.tBodies[0].appendChild(nr);
    }
  }
  var del=e.target.closest('[data-del-row]');
  if(del){var tr=del.closest('tr');if(tr)tr.remove();}
});
"""


# Reference que ve el agente — describe el framework brevemente.
# Esto va en el system prompt (cacheado) para que el agente sepa cómo usarlo.
FRAMEWORK_PROMPT = """\
============================================================
FRAMEWORK DE PROTOTIPO (preinyectado, NO lo escribas)
============================================================

Cuando emitas `prototipo_html`, NO escribas <!DOCTYPE>, <html>, <head>, <style>,
<script>, ni </html>. La fabrica los pone automaticamente. Tu output debe ser
SOLO el contenido del <body>...</body>, usando las CLASES y ATRIBUTOS del
toolkit de maqueta `f-*` que ya esta cargado. Este toolkit es NEUTRAL: que
controles son VALIDOS lo decide el <skill_de_diseno> del perfil (ver al final).

COMPONENTES DISPONIBLES:

  Header:
    <div class="f-header">
      <h1>Titulo de la app</h1>
      <div class="f-spacer"></div>
      <button class="f-btn">Acción</button>
    </div>

  Navegación por tabs (las views se muestran/ocultan con click):
    <div class="f-tabs">
      <div class="f-tab active" data-view="v1">Vista 1</div>
      <div class="f-tab" data-view="v2">Vista 2</div>
    </div>
    <main class="f-main">
      <section data-view-content="v1"> ... </section>
      <section data-view-content="v2" class="view-hidden"> ... </section>
    </main>

  Card / panel:
    <div class="f-card">
      <h2>Titulo</h2>
      <h3>Subtitulo</h3>
      ... contenido ...
    </div>

  Toolbar (filtros, acciones):
    <div class="f-toolbar">
      <button class="f-btn primary" data-add-row="t-lista">+ Crear</button>
      <button class="f-btn">Exportar</button>
    </div>

  Botones: clase `f-btn`. Variantes: `.primary`, `.danger`, `.icon`.

  Tabla editable con matchcode y cálculos:
    <table class="f-table" id="t-lista">
      <thead>
        <tr><th>Tractor</th><th>Implemento</th><th>Horas</th><th></th></tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <div class="f-mc">
              <input class="f-input" id="t1-tractor" value="T001"/>
              <button class="f-btn" data-matchcode="dlg-tractor">🔍</button>
            </div>
          </td>
          <td><input class="f-input" value="Arado"/></td>
          <td class="ro">12.5</td>
          <td><div class="f-row-actions">
            <button class="f-btn icon danger" data-del-row>🗑</button>
          </div></td>
        </tr>
      </tbody>
    </table>

  Inputs: clase `f-input` y `f-select`. Celdas readonly: `class="ro"`.

  Matchcode (input + boton F4 abre dialog):
    <div class="f-mc">
      <input class="f-input" id="campo-x" value=""/>
      <button class="f-btn" data-matchcode="dlg-x">🔍</button>
    </div>
    ...
    <div class="f-dialog-bg" id="dlg-x" data-target="#campo-x">
      <div class="f-dialog">
        <div class="f-dialog-h">Selecciona...</div>
        <div class="f-dialog-b">
          <table class="f-table">
            <tr data-mc-select="T001"><td>T001</td><td>John Deere 6110</td></tr>
            <tr data-mc-select="T002"><td>T002</td><td>Massey 4710</td></tr>
          </table>
        </div>
        <div class="f-dialog-f"><button class="f-btn" data-close>Cancelar</button></div>
      </div>
    </div>

  KPI cards (analytics) — ⚠️ PATRON ANALITICO MODERNO (Fiori/web). SOLO usar si
  el <skill_de_diseno> lo permite. NO EXISTE en SAP GUI clasico/Dynpro: si el
  stack es clasico, NO uses f-kpi/f-kpis (nada de "numeros grandes" en tarjetas):
    <div class="f-kpis">
      <div class="f-kpi"><div class="lbl">Horas Hoy</div><div class="val">142.5</div></div>
      <div class="f-kpi"><div class="lbl">Tractores</div><div class="val">8</div></div>
    </div>

  Badges:
    <span class="f-badge ok">Activo</span>
    <span class="f-badge warn">Pendiente</span>
    <span class="f-badge error">Falla</span>

REGLAS:
- NO inventes CSS inline ni <style> propio salvo casos puntuales (color de
  banner, etc.).
- USA `data-view`/`data-view-content` para tabs. `data-matchcode`+
  `data-target` para F4. `data-add-row`/`data-del-row` para filas.
- Si necesitas un gráfico (barras, donut), hazlo con divs y CSS Grid o
  con un SVG inline pequeño. NO uses canvas ni librerias.
- Para datos de ejemplo, usa valores realistas pero no PII real. Comenta
  con <!-- DATA: simulada -->.
- Estructura tu output con comentarios: <!-- VISTA: nombre -->.

EJEMPLO MINIMO COMPLETO (lo que va en prototipo_html):

<div class="f-header">
  <h1>Mi App</h1>
  <div class="f-spacer"></div>
</div>
<div class="f-tabs">
  <div class="f-tab active" data-view="lista">Lista</div>
</div>
<main class="f-main">
  <section data-view-content="lista">
    <div class="f-card">
      <div class="f-toolbar">
        <button class="f-btn primary" data-add-row="t1">+ Crear</button>
      </div>
      <table class="f-table" id="t1">
        <thead><tr><th>Campo</th><th></th></tr></thead>
        <tbody><tr><td><input class="f-input" value=""/></td><td></td></tr></tbody>
      </table>
    </div>
  </section>
</main>

============================================================
FIDELIDAD AL STACK — la dicta el <skill_de_diseno> (REGLA DURA)
============================================================

Este toolkit f-* es NEUTRAL (una maqueta de comunicacion). Que controles y
patrones son VALIDOS lo define el <skill_de_diseno> inyectado en tu contexto
(SAP Fiori Elements, ABAP clasico/Dynpro, web React, etc., segun el perfil del
requisito). Por eso:

- SIGUE AL PIE las reglas de fidelidad del <skill_de_diseno>: declara el tipo de
  pantalla/floorplan por vista como te indique y usa SOLO los controles que ese
  skill permite para el stack objetivo, mapeados a las clases f-*.
- NO inventes controles ni metas patrones de OTRO stack (p.ej. Fiori en un
  desarrollo clasico, o al reves).
- Si una interaccion que la spec pide NO es achievable en el stack objetivo, NO
  dibujes un control falso: anotala con un comentario <!-- FUERA-DE-STACK: ... -->
  y, si es central, responde mode="blocked".

Si NO se inyecto un <skill_de_diseno>, usa controles sobrios y genericos y no
asumas un stack concreto.
"""


# JS extra que se inyecta junto al framework para PERMITIR ANOTACIONES desde
# el inbox: cuando el usuario selecciona texto dentro del iframe del
# prototipo, mandamos un postMessage al parent con el texto seleccionado.
# El inbox escucha y muestra el botón "Comentar".
_ANNOTATION_JS = """\
(function(){
  function emitSelection(){
    var sel=window.getSelection();
    var txt=sel?sel.toString().trim():'';
    if(!txt)return;
    var rect=null;
    try{var r=sel.getRangeAt(0).getBoundingClientRect();rect={top:r.top,left:r.left,bottom:r.bottom,right:r.right,width:r.width,height:r.height};}catch(e){}
    parent.postMessage({type:'fabro:selection',target:'prototipo',text:txt.slice(0,2000),rect:rect},'*');
  }
  document.addEventListener('mouseup',function(){setTimeout(emitSelection,10);});
  document.addEventListener('keyup',function(e){if(e.shiftKey||e.key==='Shift')setTimeout(emitSelection,10);});
})();
"""


def wrap(body_html: str, title: str = "Prototipo") -> str:
    """Envuelve el contenido del agente con el shell completo del framework."""
    return (
        "<!DOCTYPE html><html lang=\"es\"><head>"
        "<meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>"
        f"<title>{title}</title>"
        f"<style>{_CSS}</style>"
        "</head><body>"
        f"{body_html}"
        f"<script>{_JS}</script>"
        f"<script>{_ANNOTATION_JS}</script>"
        "</body></html>"
    )
