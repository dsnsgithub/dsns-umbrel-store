const express = require('express');
const fs = require('fs');
const path = require('path');
const app = express();

const CONFIG_PATH = path.join('/app/data/config.json');

app.use(express.static('/app/ui'));
app.use(express.json());

app.get('/api/config', (req, res) => {
  fs.readFile(CONFIG_PATH, 'utf8', (err, data) => {
    if (err) return res.status(500).send('Error reading config');
    res.type('application/json').send(data);
  });
});

app.post('/api/config', (req, res) => {
  const newConfig = JSON.stringify(req.body, null, 2);
  fs.writeFile(CONFIG_PATH, newConfig, 'utf8', (err) => {
    if (err) return res.status(500).send('Error saving config');
    res.send({ status: 'success' });
  });
});

app.listen(3000, () => {
  console.log('Web UI running on http://localhost:3000');
});