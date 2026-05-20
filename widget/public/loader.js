// widget/public/loader.js
(function () {
  var script = document.currentScript;
  if (!script) return;

  var widgetId = script.getAttribute('data-widget-id') || 'demo';
  var apiBase =
    script.getAttribute('data-api-base') || 'http://localhost:8000';

  // Create the iframe that will load the full widget app
  var iframe = document.createElement('iframe');
  iframe.src = apiBase + '/widget/' + widgetId + '/embed';
  iframe.style.border = 'none';
  iframe.style.width = '100%';
  iframe.style.height = '500px';
  iframe.style.overflow = 'hidden';
  iframe.setAttribute('allow', 'clipboard-write');

  // Append the iframe right after the loader script tag
  script.parentNode.insertBefore(iframe, script.nextSibling);

  // Optional: listen for resize requests from the widget (postMessage)
  window.addEventListener('message', function (event) {
    if (!event.data || typeof event.data !== 'object') return;
    if (event.data.type === 'mc:resize' && event.data.height) {
      iframe.style.height = event.data.height + 'px';
    }
  });
})();