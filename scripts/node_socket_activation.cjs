'use strict';

// Systemd owns and binds the OLS-facing Unix socket. Keep PORT numeric for
// application compatibility, but redirect the conventional server.listen
// call for that exact port to the inherited activation descriptor.
const net = require('net');
const originalListen = net.Server.prototype.listen;
const assignedPort = Number.parseInt(process.env.PORT || '', 10);
const listenFds = Number.parseInt(process.env.LISTEN_FDS || '0', 10);
const activationFd = 3;

function requestsAssignedPort(args) {
  if (!Number.isInteger(assignedPort) || listenFds < 1 || args.length === 0) return false;
  const first = args[0];
  if (typeof first === 'number' || typeof first === 'string') return Number(first) === assignedPort;
  return first && typeof first === 'object' && Number(first.port) === assignedPort;
}

net.Server.prototype.listen = function boronSocketListen(...args) {
  if (!requestsAssignedPort(args)) return originalListen.apply(this, args);
  const callback = typeof args[args.length - 1] === 'function' ? args[args.length - 1] : undefined;
  const options = (args[0] && typeof args[0] === 'object') ? {...args[0]} : {};
  delete options.port;
  delete options.path;
  delete options.host;
  options.fd = activationFd;
  options.exclusive = false;
  return callback ? originalListen.call(this, options, callback) : originalListen.call(this, options);
};
