// widget/src/widget.jsx
import { render } from 'preact';

function App() {
  return <div style={{ padding: '1rem' }}>Widget placeholder</div>;
}

const root = document.getElementById('mc-widget-root');
if (root) {
  render(<App />, root);
}