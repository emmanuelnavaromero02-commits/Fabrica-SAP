# Freestyle SAPUI5 — only with G4 justification

If you're reading this, G4 should have already justified why Fiori Elements is insufficient. If it didn't, go back and re-do G4.

## When freestyle is the right choice

Genuine cases:
- Multi-step wizard with conditional branches.
- Dashboard with custom widgets (drag-drop, drawing canvas).
- High-volume data entry with non-tabular layout.
- Integration with a non-OData backend (rare in ZDSWF, but possible).
- Mobile-web specific UX that MDK can't deliver.

Non-cases (force back to Fiori Elements):
- "The customer wants a different layout" → CSS / FPM custom section.
- "We need a filter that FE doesn't support" → custom selection field or value help.
- "We want a chart" → ALP or OVP.
- "We need a specific button" → action with annotation.

## Project structure

```
zdswf-<app>/
  webapp/
    Component.js
    manifest.json
    i18n/
      i18n.properties
      i18n_es.properties
    view/
      App.view.xml
      Main.view.xml
    controller/
      App.controller.js
      Main.controller.js
    model/
      models.js
      formatter.js
    fragment/
      ConfirmDialog.fragment.xml
    css/
      style.css
    test/
      unit/
      integration/
  package.json
  ui5.yaml
  xs-app.json     " for BTP / approuter
  mta.yaml        " for BTP CF deploy
```

## Component.js

```js
sap.ui.define([
  "sap/ui/core/UIComponent",
  "sap/ui/model/json/JSONModel",
  "zdswf/<app>/model/models"
], function (UIComponent, JSONModel, models) {
  "use strict";
  return UIComponent.extend("zdswf.<app>.Component", {
    metadata: { manifest: "json" },
    init: function () {
      UIComponent.prototype.init.apply(this, arguments);
      this.setModel(models.createDeviceModel(), "device");
      this.getRouter().initialize();
    }
  });
});
```

## manifest.json — V4 OData binding

```json
{
  "sap.app": {
    "id": "zdswf.<app>",
    "type": "application",
    "dataSources": {
      "mainService": {
        "uri": "/sap/opu/odata4/sap/zdswf_sb_<name>/srvd/sap/zdswf_sd_<name>/0001/",
        "type": "OData",
        "settings": { "odataVersion": "4.0" }
      }
    }
  },
  "sap.ui5": {
    "rootView": { "viewName": "zdswf.<app>.view.App", "type": "XML", "id": "app" },
    "models": {
      "i18n": {
        "type": "sap.ui.model.resource.ResourceModel",
        "settings": { "bundleName": "zdswf.<app>.i18n.i18n" }
      },
      "": {
        "dataSource": "mainService",
        "settings": {
          "synchronizationMode": "None",
          "operationMode": "Server",
          "autoExpandSelect": true,
          "earlyRequests": true,
          "groupProperties": { "default": { "submit": "Auto" } }
        }
      }
    },
    "routing": {
      "config": {
        "routerClass": "sap.m.routing.Router",
        "viewType": "XML",
        "viewPath": "zdswf.<app>.view",
        "controlId": "app",
        "controlAggregation": "pages",
        "async": true
      },
      "routes": [
        { "pattern": "", "name": "main", "target": "main" }
      ],
      "targets": {
        "main": { "viewName": "Main", "viewLevel": 1 }
      }
    }
  }
}
```

## XML view

```xml
<mvc:View
  controllerName="zdswf.<app>.controller.Main"
  xmlns:mvc="sap.ui.core.mvc"
  xmlns="sap.m"
  xmlns:f="sap.f">
  <Page title="{i18n>page.title}">
    <List id="entityList"
          items="{path: '/<Entity>', parameters: {$count: true}}"
          growing="true"
          growingThreshold="50">
      <StandardListItem
        title="{<BusinessField>}"
        description="{Amount}" />
    </List>
  </Page>
</mvc:View>
```

## Controller — keep it thin

```js
sap.ui.define([
  "sap/ui/core/mvc/Controller",
  "sap/m/MessageBox"
], function (Controller, MessageBox) {
  "use strict";
  return Controller.extend("zdswf.<app>.controller.Main", {
    onInit: function () {
      // wire up
    },
    onPress: function (oEvent) {
      const oContext = oEvent.getSource().getBindingContext();
      // delegate to RAP action via OData operation binding
      const oOperation = this.getView().getModel().bindContext(
        "com.sap.gateway.srvd.zdswf_sd_<name>.v0001.Approve(...)",
        oContext
      );
      oOperation.execute().then(
        () => MessageBox.success(this.getView().getModel("i18n").getResourceBundle().getText("msg.approved")),
        (oError) => MessageBox.error(oError.message)
      );
    }
  });
});
```

## Fragments and dialogs

```js
onOpenDialog: async function () {
  if (!this._oDialog) {
    this._oDialog = await this.loadFragment({
      name: "zdswf.<app>.fragment.ConfirmDialog"
    });
  }
  this._oDialog.open();
}
```

## Forbidden in freestyle (ZDSWF policy)

- Calling RAP business logic from JS — go through OData actions.
- Multiple OData V2 fallbacks.
- jQuery direct DOM manipulation outside `onAfterRendering`.
- Inline event handlers in XML with logic (only `.handlerName` references).
- `console.log` left in code.
- Untyped formatters — every `formatter.js` function has JSDoc with types.

## Deployment

| Target | Path |
|---|---|
| Embedded in S/4HANA | BSP application via `abap_repository_client` adt task |
| BTP Cloud Foundry | `mta.yaml` build via `mbt build`, deploy via `cf deploy` |
| Standalone HTML5 app via Build Workzone | upload via `mta` to Build Workzone HTML5 Apps Repository |

## Testing

OPA5 integration tests under `webapp/test/integration/` are mandatory for freestyle. ZDSWF threshold: at least one journey per route.

```js
// webapp/test/integration/AllJourneys.js
sap.ui.require([
  "sap/ui/test/Opa5",
  "zdswf/<app>/test/integration/pages/Main",
  "zdswf/<app>/test/integration/journeys/MainJourney"
], function (Opa5) {
  "use strict";
  Opa5.extendConfig({
    arrangements: new sap.ui.test.Opa5(),
    viewNamespace: "zdswf.<app>.view.",
    autoWait: true
  });
});
```
