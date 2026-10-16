from __future__ import annotations

_CSS = """\
:root {
  --b: #0a6ed1;
  --b2: #084788;
  --bg: #fafafa;
  --sf: #fff;
  --bo: #e0e0e0;
  --t: #333;
  --tm: #666;
  --ok: #107e3e;
  --wn: #df6e0c;
  --er: #bb0000;
  --r: 4px;
  --g: 8px;
  font-family: "72", "Helvetica Neue", Arial, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--t); font-size: 14px; line-height: 1.5; }
.f-header {
  background: var(--sf); border-bottom: 1px solid var(--bo);
  padding: 12px 24px; display: flex; align-items: center; gap: 16px;
}
.f-header h1 { font-size: 16px; font-weight: 600; }
.f-spacer { flex: 1; }
.f-tabs { display: flex; background: var(--sf); border-bottom: 1px solid var(--bo); }
.f-tab {
  padding: 12px 20px; cursor: pointer; border-bottom: 3px solid transparent;
  color: var(--tm); font-weight: 500; user-select: none;
}
.f-tab:hover { background: #f5f8fc; }
.f-tab.active { border-bottom-color: var(--b); color: var(--b); }
.f-main { padding: 16px 24px; }
.f-card {
  background: var(--sf); border: 1px solid var(--bo); border-radius: var(--r);
  padding: 16px; margin-bottom: 16px;
}
.f-card h2 { font-size: 16px; font-weight: 600; margin-bottom: 12px; color: var(--t); }
.f-card h3 {
  font-size: 13px; font-weight: 600; color: var(--tm); text-transform: uppercase;
  letter-spacing: .5px; margin: 8px 0;
}
.f-toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 0; flex-wrap: wrap; }
.f-btn {
  padding: 6px 14px; border-radius: var(--r); border: 1px solid var(--bo);
  background: var(--sf); cursor: pointer; font-size: 13px; color: var(--t);
}
.f-btn:hover { background: #f0f0f0; }
.f-btn.primary { background: var(--b); color: #fff; border-color: var(--b); }
.f-btn.primary:hover { background: var(--b2); }
.f-btn.danger { color: var(--er); }
.f-btn.icon { padding: 4px 8px; }
.f-table { width: 100%; border-collapse: collapse; background: var(--sf); }
.f-table th {
  text-align: left; padding: 10px; background: #f5f5f5; border-bottom: 1px solid var(--bo);
  font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .3px;
}
.f-table td { padding: 6px 10px; border-bottom: 1px solid var(--bo); vertical-align: middle; }
.f-table tr:hover td { background: #fafbff; }
.f-table .ro { background: #f5f5f5; color: var(--tm); }
.f-input, .f-select {
  width: 100%; padding: 6px 8px; border: 1px solid var(--bo); border-radius: var(--r);
  font-size: 13px; background: #fff; font-family: inherit;
}
.f-input:focus, .f-select:focus { border-color: var(--b); outline: none; }
.f-mc { display: flex; gap: 0; }
.f-mc .f-input { border-right: none; border-radius: var(--r) 0 0 var(--r); }
.f-mc .f-btn {
  border-radius: 0 var(--r) var(--r) 0; border-left-color: var(--bo); padding: 6px 10px;
}
.f-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; }
.f-badge.ok { background: #e8f5e9; color: var(--ok); }
.f-badge.warn { background: #fff3e0; color: var(--wn); }
.f-badge.error { background: #ffebee; color: var(--er); }
.f-dialog-bg {
  position: fixed; inset: 0; background: rgba(0,0,0,.3); display: none;
  align-items: center; justify-content: center; z-index: 100;
}
.f-dialog-bg.open { display: flex; }
.f-dialog {
  background: #fff; border-radius: var(--r); width: 600px; max-width: 90vw;
  max-height: 80vh; display: flex; flex-direction: column;
}
.f-dialog-h {
  padding: 14px 20px; border-bottom: 1px solid var(--bo); font-weight: 600; display: flex;
}
.f-dialog-b { padding: 16px 20px; overflow: auto; flex: 1; }
.f-dialog-f { padding: 12px 20px; border-top: 1px solid var(--bo); text-align: right; }
.f-kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.f-kpi {
  background: var(--sf); border: 1px solid var(--bo); border-radius: var(--r); padding: 14px;
}
.f-kpi .lbl {
  font-size: 11px; color: var(--tm); text-transform: uppercase; letter-spacing: .5px;
}
.f-kpi .val { font-size: 24px; font-weight: 600; color: var(--b); margin-top: 4px; }
.f-form { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.f-form label { font-size: 12px; color: var(--tm); font-weight: 600; margin-bottom: 4px; }
.f-hint { font-size: 12px; color: var(--tm); margin-top: 4px; }
.f-row-actions { display: flex; gap: 4px; justify-content: flex-end; }
.view-hidden { display: none !important; }
"""

_JS = """\
document.addEventListener('click', function(e) {
  var tab = e.target.closest('.f-tab');
  if (tab) {
    var v = tab.dataset.view;
    document.querySelectorAll('.f-tab').forEach(function(t) {
      t.classList.toggle('active', t === tab);
    });
    document.querySelectorAll('[data-view-content]').forEach(function(s) {
      s.classList.toggle('view-hidden', s.dataset.viewContent !== v);
    });
  }
  var mc = e.target.closest('[data-matchcode]');
  if (mc) {
    var id = mc.dataset.matchcode;
    var dlg = document.getElementById(id);
    if (dlg) dlg.classList.add('open');
  }
  if (e.target.matches('.f-dialog-bg') || e.target.closest('[data-close]')) {
    var bg = e.target.closest('.f-dialog-bg');
    if (bg) bg.classList.remove('open');
  }
  var sel = e.target.closest('[data-mc-select]');
  if (sel) {
    var val = sel.dataset.mcSelect;
    var dlg2 = sel.closest('.f-dialog-bg');
    var tgt = dlg2 && dlg2.dataset.target ? document.querySelector(dlg2.dataset.target) : null;
    if (tgt) tgt.value = val;
    if (dlg2) dlg2.classList.remove('open');
  }
  var add = e.target.closest('[data-add-row]');
  if (add) {
    var tid = add.dataset.addRow;
    var tbl = document.getElementById(tid);
    if (tbl && tbl.tBodies[0] && tbl.tBodies[0].rows[0]) {
      var nr = tbl.tBodies[0].rows[0].cloneNode(true);
      nr.querySelectorAll('input, select').forEach(function(i) { i.value = ''; });
      tbl.tBodies[0].appendChild(nr);
    }
  }
  var del = e.target.closest('[data-del-row]');
  if (del) {
    var tr = del.closest('tr');
    if (tr) tr.remove();
  }
});
"""


def wrap_prototype(body_html: str, title: str = "Prototipo SAP Fiori") -> str:
    if "<html" in body_html.lower() or "<!doctype" in body_html.lower():
        return body_html
    return (
        '<!DOCTYPE html><html lang="es"><head>'
        '<meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{title}</title>"
        f"<style>{_CSS}</style>"
        "</head><body>"
        f"{body_html}"
        f"<script>{_JS}</script>"
        "</body></html>"
    )
