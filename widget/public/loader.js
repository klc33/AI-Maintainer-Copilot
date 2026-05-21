// widget/public/loader.js
// Served by the widget nginx at /widget.js. This is the ONLY URL hosts
// embed; everything else (iframe, JWT minting, theme) is hidden behind it.
//
// Behavior:
//   1. Read data-widget-id (and optionally data-api-base) from the <script>.
//   2. Inject an iframe at the bottom-right of the host page, sized to the
//      collapsed bubble (~96x96 px).
//   3. Listen for `mc:layout` messages from the widget inside that iframe.
//      The widget owns its sizing and corner choice (theme.position); the
//      loader just applies them to the iframe element.
(function () {
  var script = document.currentScript;
  if (!script) return;

  var widgetId = script.getAttribute('data-widget-id') || 'demo';
  var apiBase = script.getAttribute('data-api-base') || 'http://localhost:8000';

  var iframe = document.createElement('iframe');
  iframe.src = apiBase + '/widget/' + widgetId + '/embed';
  iframe.title = 'chat widget';
  iframe.setAttribute('allow', 'clipboard-write');
  // The host page should be able to see through the empty padding around
  // the bubble — transparent background + no border.
  iframe.setAttribute('allowtransparency', 'true');
  iframe.style.cssText = [
    'position: fixed',
    'bottom: 0',
    'right: 0',
    'width: 96px',
    'height: 96px',
    'border: none',
    'background: transparent',
    'color-scheme: normal',
    'z-index: 2147483600',
  ].join(';');

  document.body.appendChild(iframe);

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || typeof data !== 'object') return;
    // Single channel: the widget reports desired iframe dimensions and
    // which corner of the viewport to pin to.
    if (data.type !== 'mc:layout') return;
    if (typeof data.width  === 'number') iframe.style.width  = data.width  + 'px';
    if (typeof data.height === 'number') iframe.style.height = data.height + 'px';
    if (data.position === 'bottom-left') {
      iframe.style.left = '0'; iframe.style.right = 'auto';
    } else if (data.position === 'bottom-right') {
      iframe.style.right = '0'; iframe.style.left = 'auto';
    }
  });
})();
