"""DesiredState -> launcher.js, served by sync at /__edge/launcher.js (decision 13).

The Umbrel dashboard opens an app with window.open at <current host>:<port>. This script
rewrites that to the app's own hostname. Every other URL passes through untouched.
"""

from __future__ import annotations

import json

_TEMPLATE = """\
(function () {
  "use strict";
  var map = __MAP__;
  var host = window.location.hostname;
  function rewrite(raw) {
    try {
      var url = new URL(raw, window.location.href);
      if (url.hostname !== host || !url.port) return raw;
      if (!Object.prototype.hasOwnProperty.call(map, url.port)) return raw;
      return "https://" + map[url.port] + url.pathname + url.search;
    } catch (err) {
      return raw;
    }
  }
  var open = window.open;
  window.open = function (url) {
    var args = Array.prototype.slice.call(arguments);
    if (typeof args[0] === "string") args[0] = rewrite(args[0]);
    return open.apply(this, args);
  };
  document.addEventListener(
    "click",
    function (event) {
      var node = event.target;
      var link = node && node.closest ? node.closest("a[href]") : null;
      if (!link) return;
      var href = link.getAttribute("href");
      var next = rewrite(href);
      if (next !== href) link.setAttribute("href", next);
    },
    true
  );
})();
"""


def render(port_map: dict[int, str]) -> str:
    """port_map is host port to hostname. Keys are strings in the script, as URL.port is."""
    entries = {str(port): host for port, host in sorted(port_map.items())}
    return _TEMPLATE.replace("__MAP__", json.dumps(entries, indent=2, sort_keys=False))
