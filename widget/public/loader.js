// loader.js - will be served as /widget.js
(function() {
  const scriptTag = document.currentScript;
  const widgetId = scriptTag.getAttribute('data-widget-id');
  const apiBase = scriptTag.getAttribute('data-api-base') || 'http://localhost:8000';

  // Inject iframe
  const iframe = document.createElement('iframe');
  iframe.src = `${apiBase}/widget/${widgetId}/embed`;
  iframe.style.border = 'none';
  iframe.style.width = '100%';
  iframe.style.height = '400px';
  document.body.appendChild(iframe);
})();