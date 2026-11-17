# noVNC in the Android app (assets/vnc)

- Project: noVNC, https://github.com/novnc/noVNC (MPL-2.0 for core/, see LICENSE.txt in this folder)
- Release: tag v1.5.0 (https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz)
- Archive sha256: 6a73e41f98388a5348b7902f54b02d177cb73b7e5eb0a7a0dcf688cc2c79b42a
- Copied as they are: core/ and vendor/pako/ (the pako zlib that core/inflator.js and core/deflator.js import), and LICENSE.txt.
- Kelo's own files here: view.html and view.js (the page that drives RFB over the app's bridge; see chat.kelo.app.agent.VncRelay).
