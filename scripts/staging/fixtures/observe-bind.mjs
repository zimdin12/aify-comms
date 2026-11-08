// Observation only, in the owned candidate process. Never changes listen arguments or results.
import fs from 'node:fs';
import net from 'node:net';
const emit = net.Server.prototype.emit;
net.Server.prototype.emit = function (event, ...args) {
  if (event === 'listening' && this.address()?.port === 8802) {
    fs.writeFileSync('/work/candidate-bind.json', JSON.stringify({ pid: process.pid, atMs: Date.now() }));
  }
  return emit.call(this, event, ...args);
};
