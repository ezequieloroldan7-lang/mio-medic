/* theme.js — aplica el tema (light/dark) ANTES del primer paint para
   evitar FOUC. Se carga sincrono en <head>, antes del stylesheet.

   Fuente de verdad:
   - localStorage.theme === "light" | "dark" → override explícito del usuario
   - ausencia → sigue prefers-color-scheme (auto)

   API pública expuesta en window.__theme:
     get()     → "light" | "dark" efectivo actual
     set(t)    → "light" | "dark" | null (null = volver a auto)
     toggle()  → flipa entre light y dark (siempre explícito)
     onChange(fn) → suscribe a cambios (explícitos o del SO)
*/
(function () {
  var STORAGE_KEY = "theme";
  var mql = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var listeners = [];

  function getStored() {
    try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; }
  }

  function apply(theme) {
    if (theme === "light" || theme === "dark") {
      document.documentElement.setAttribute("data-theme", theme);
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function effective() {
    var stored = getStored();
    if (stored === "light" || stored === "dark") return stored;
    return (mql && mql.matches) ? "dark" : "light";
  }

  function notify() {
    var t = effective();
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](t); } catch (e) {}
    }
  }

  // Aplicar ya, antes del primer paint
  apply(getStored());

  // Seguir los cambios del SO si el usuario NO hizo override
  if (mql && mql.addEventListener) {
    mql.addEventListener("change", function () {
      if (!getStored()) notify();
    });
  }

  window.__theme = {
    get: effective,
    set: function (theme) {
      if (theme === "light" || theme === "dark") {
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}
      } else {
        try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
      }
      apply(theme);
      notify();
    },
    toggle: function () {
      this.set(effective() === "dark" ? "light" : "dark");
    },
    onChange: function (fn) {
      if (typeof fn === "function") listeners.push(fn);
    }
  };
})();
