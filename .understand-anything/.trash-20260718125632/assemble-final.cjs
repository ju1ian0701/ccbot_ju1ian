#!/usr/bin/env node
const fs = require('fs');
const root = 'D:\\CCbot_tmux\\ccbot\\ccbot_ju1ian';
const graph = JSON.parse(fs.readFileSync(root + '\\.understand-anything\\intermediate\\assembled-graph.json', 'utf8'));
const layersRaw = JSON.parse(fs.readFileSync(root + '\\.understand-anything\\intermediate\\layers.json', 'utf8'));
const tourRaw = JSON.parse(fs.readFileSync(root + '\\.understand-anything\\intermediate\\tour.json', 'utf8'));
const layers = Array.isArray(layersRaw) ? layersRaw : (layersRaw.layers || []);
const tour = Array.isArray(tourRaw) ? tourRaw : (tourRaw.steps || []);
graph.nodes.forEach(n => {
  if (!n.name) n.name = n.filePath ? n.filePath.split('/').pop() : n.id.split(':').pop();
  if (!n.tags || !n.tags.length) n.tags = ['untagged'];
  if (!n.summary) n.summary = 'File: ' + (n.filePath || n.id);
  if (n.type === 'node' || n.type === 'state_file') n.type = 'file';
});
graph.edges.forEach(e => {
  if (!e.type || e.type === '') e.type = 'related';
  if (!e.weight) e.weight = 0.5;
  if (!e.direction) e.direction = 'forward';
});
const result = { version: '1.0.0', project: { name: 'ccbot', languages: ['python','markdown','json','yaml','toml','javascript','shell','txt','otf'], frameworks: ['python-telegram-bot'], description: 'Telegram Bot for monitoring Claude Code sessions', analyzedAt: new Date().toISOString(), gitCommitHash: '96a7eea25f86399fec9873dfb263c3b2856c4987' }, nodes: graph.nodes, edges: graph.edges, layers, tour };
fs.writeFileSync(root + '\\.understand-anything\\intermediate\\assembled-graph.json', JSON.stringify(result, null, 2), 'utf8');
console.log('Assembled: ' + result.nodes.length + ' nodes, ' + result.edges.length + ' edges, ' + result.layers.length + ' layers, ' + result.tour.length + ' tour steps');
