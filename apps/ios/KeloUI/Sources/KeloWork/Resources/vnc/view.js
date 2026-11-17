// Kelo's VNC page. noVNC (core/, vendor/pako: see SOURCE.md) draws the browser tab's X screen. The app's native side owns the socket:
// the bytes reach this page as strings (JSON, the binary as base64), so nothing here opens a connection. Android: a WebMessagePort.
// iPhone: page -> app is window.webkit.messageHandlers.keloVnc.postMessage(string), app -> page calls window.keloVncReceive(string).
//
// From the app:  {"t":"open"}  create the connection now;  {"t":"hold","on":bool}  the owner holds the tab (input allowed);
//                {"t":"reset"}  a new socket for a new screen size: start a new session, keep the old picture until it connects;
//                {"t":"d","b":"<base64>"}  bytes from the server.
// To the app:    {"t":"ready"}  the port works;  {"t":"d","b":"<base64>"}  bytes for the server.
import RFB from './core/rfb.js';

const screen = document.getElementById('screen');
let port = null;
// the iPhone's script message handler (undefined in Android's WebView)
const native = window.webkit && window.webkit.messageHandlers ? window.webkit.messageHandlers.keloVnc : undefined;
let holding = false;

function post(obj) {
    if (port) port.postMessage(JSON.stringify(obj));
    else if (native) native.postMessage(JSON.stringify(obj));
}

function toBase64(u8) {
    let s = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < u8.length; i += CHUNK) s += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK));
    return btoa(s);
}

function fromBase64(b64) {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return u8.buffer;
}

// The channel noVNC talks through: the same shape as a WebSocket (readyState 0 connecting, 1 open, 3 closed). One per session.
function makeChannel() {
    return {
        binaryType: 'arraybuffer',
        protocol: '',
        readyState: 0,
        onopen: null,
        onmessage: null,
        onclose: null,
        onerror: null,
        send(data) {
            const u8 = data instanceof Uint8Array ? data : new Uint8Array(data);
            post({ t: 'd', b: toBase64(u8) });
        },
        close() {
            this.readyState = 3;
        },
    };
}

// The session the server's bytes go to. After {"t":"reset"} (the app opened a new socket for a new screen size) a new session starts in a
// hidden holder next to the old one; the old picture stays until the new session has connected, then the new one is shown.
let channel = null;
let current = null;

function start(replacing) {
    const old = replacing ? current : null;
    if (old) {
        // nothing of the old session may reach the new socket, and it takes no more input
        old.channel.send = () => {};
        old.rfb.viewOnly = true;
    }
    const holder = document.createElement('div');
    holder.style.cssText = 'position:absolute;inset:0' + (old ? ';visibility:hidden' : '');
    screen.appendChild(holder);
    const ch = makeChannel();
    const rfb = new RFB(holder, ch, { shared: true });
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.clipViewport = false;
    rfb.viewOnly = !holding;
    rfb.qualityLevel = 6;
    rfb.compressionLevel = 2;
    rfb.focusOnClick = false;
    rfb.background = 'transparent';
    channel = ch;
    current = { rfb, channel: ch, holder };
    ch.readyState = 1;
    if (ch.onopen) ch.onopen();
    if (old) {
        let swapped = false;
        const swap = () => {
            if (swapped) return;
            swapped = true;
            holder.style.visibility = 'visible';
            try { old.rfb.disconnect(); } catch (e) { /* already gone */ }
            old.holder.remove();
        };
        // a beat after the connection, so the new picture has been drawn; never keep the old one for ever
        rfb.addEventListener('connect', () => setTimeout(swap, 200));
        setTimeout(swap, 4000);
    }
}

function open() {
    if (current) return;
    start(false);
}

function fromNative(text) {
    let m;
    try {
        m = JSON.parse(text);
    } catch (e) {
        return;
    }
    if (m.t === 'open') open();
    else if (m.t === 'reset') start(true);
    else if (m.t === 'hold') {
        holding = !!m.on;
        if (current) current.rfb.viewOnly = !holding;
    } else if (m.t === 'd') {
        if (channel && channel.onmessage) channel.onmessage({ data: fromBase64(m.b) });
    }
}

window.addEventListener('message', (e) => {
    if (port || !e.ports || e.ports.length === 0) return;
    port = e.ports[0];
    port.onmessage = (ev) => fromNative(ev.data);
    post({ t: 'ready' });
});

// iPhone: the app calls this with each message; it is defined before "ready" is sent
if (native) {
    window.keloVncReceive = fromNative;
    post({ t: 'ready' });
}
