async function loadConfig() {
  const response = await fetch('/api/config');
  const text = await response.text();
  document.getElementById('config').value = text;
}

async function saveConfig() {
  try {
    const configText = document.getElementById('config').value;
    const json = JSON.parse(configText);
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(json)
    });
    const data = await res.json();
    document.getElementById('status').innerText = 'Saved successfully!';
  } catch (err) {
    document.getElementById('status').innerText = 'Invalid JSON or failed to save.';
  }
}

window.onload = loadConfig;