const fs = require('fs');
const path = require('path');
const net = require('net');
const http = require('http');
const https = require('https');

const TARGET_PORT = 7776;
const PROXY_PORT = 7777;

const CERT_PATH = path.join(__dirname, 'mac-mini-m1.tail178de6.ts.net.crt');
const KEY_PATH = path.join(__dirname, 'mac-mini-m1.tail178de6.ts.net.key');

const hasTlsCerts = fs.existsSync(CERT_PATH) && fs.existsSync(KEY_PATH);
const httpsOptions = hasTlsCerts ? {
  cert: fs.readFileSync(CERT_PATH),
  key: fs.readFileSync(KEY_PATH)
} : null;

// Generic proxy request handler
function proxyHandler(req, res) {
  const options = {
    hostname: '127.0.0.1',
    port: TARGET_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  proxyReq.on('error', (err) => {
    res.writeHead(502, { 'Content-Type': 'text/plain' });
    res.end('Batcomputer gateway error: ' + err.message);
  });

  req.pipe(proxyReq, { end: true });
}

// Internal HTTP and HTTPS servers
const httpServer = http.createServer(proxyHandler);
const httpsServer = hasTlsCerts ? https.createServer(httpsOptions, proxyHandler) : null;

// Multi-protocol smart listener on 0.0.0.0:7777
const smartServer = net.createServer((socket) => {
  socket.once('data', (buffer) => {
    // 0x16 is TLS Handshake record type
    if (buffer[0] === 0x16 && httpsServer) {
      httpsServer.emit('connection', socket);
    } else {
      httpServer.emit('connection', socket);
    }
    // Re-inject the buffered initial bytes into the socket stream
    socket.unshift(buffer);
  });

  socket.on('error', (err) => {
    // Suppress unhandled socket resets
  });
});

smartServer.listen(PROXY_PORT, '0.0.0.0', () => {
  console.log(`[Batcomputer] Dual HTTP/HTTPS Smart Proxy running on 0.0.0.0:${PROXY_PORT} (TLS: ${hasTlsCerts ? 'ENABLED' : 'HTTP-ONLY FALLBACK'})`);
  console.log(`[Batcomputer] Forwarding traffic to 127.0.0.1:${TARGET_PORT}`);
});
