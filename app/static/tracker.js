/*! BarePulse Tracker — cookieless, dependency-free, < 1KB gzipped. MIT. */
(function () {
  "use strict";

  var script = document.currentScript;
  var endpoint =
    (script && script.getAttribute("data-endpoint")) ||
    (script && new URL(script.src).origin + "/api/collect") ||
    "/api/collect";

  var lastPath = null;

  function payload() {
    return JSON.stringify({
      domain: location.hostname,
      path: location.pathname + location.search,
      referrer: document.referrer || "",
      screen: (screen.width || 0) + "x" + (screen.height || 0),
      language: navigator.language || ""
    });
  }

  function send() {
    // De-dupe rapid same-path SPA fires.
    var path = location.pathname + location.search;
    if (path === lastPath) return;
    lastPath = path;

    var body = payload();
    try {
      // text/plain keeps this a CORS "simple request" (no preflight) and works
      // with sendBeacon, which survives page unload.
      if (navigator.sendBeacon) {
        navigator.sendBeacon(endpoint, new Blob([body], { type: "text/plain" }));
      } else {
        fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "text/plain" },
          body: body,
          keepalive: true,
          mode: "cors",
          credentials: "omit"
        });
      }
    } catch (e) {
      /* analytics must never break the host page */
    }
  }

  // Track SPA route changes (history API + back/forward).
  function hook(type) {
    var orig = history[type];
    if (!orig) return;
    history[type] = function () {
      var r = orig.apply(this, arguments);
      send();
      return r;
    };
  }
  hook("pushState");
  hook("replaceState");
  window.addEventListener("popstate", send);

  // Initial hit (after DOM is ready enough to read location/referrer).
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", send);
  } else {
    send();
  }
})();
