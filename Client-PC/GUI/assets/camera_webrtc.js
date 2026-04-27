(function () {
  const peers = new Map();

  function waitForIceGatheringComplete(pc, timeoutMs) {
    if (pc.iceGatheringState === "complete") {
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        pc.removeEventListener("icegatheringstatechange", onStateChange);
        resolve();
      }, timeoutMs);

      function onStateChange() {
        if (pc.iceGatheringState === "complete") {
          window.clearTimeout(timer);
          pc.removeEventListener("icegatheringstatechange", onStateChange);
          resolve();
        }
      }

      pc.addEventListener("icegatheringstatechange", onStateChange);
    });
  }

  function updateState(source, text) {
    const stateNode = document.querySelector(`[data-webrtc-state="${source}"]`);
    if (stateNode) {
      stateNode.textContent = text;
    }
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || body.message || `Request failed (${response.status})`);
    }
    return body;
  }

  async function requestOfferRestart(source) {
    await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  }

  async function connectSource(source, video) {
    let entry = peers.get(source);
    if (!entry) {
      entry = {
        source,
        video,
        pc: null,
        stream: null,
        signalId: "",
        answeredSignalId: "",
        inFlight: false,
        lastRestartAt: 0,
      };
      peers.set(source, entry);
    } else {
      entry.video = video;
      if (entry.stream && entry.video.srcObject !== entry.stream) {
        entry.video.srcObject = entry.stream;
      }
    }

    if (entry.inFlight) {
      return;
    }

    if (
      entry.pc &&
      entry.signalId &&
      !["failed", "disconnected", "closed"].includes(entry.pc.connectionState)
    ) {
      return;
    }

    entry.inFlight = true;
    try {
      const offer = await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/offer`, {
        cache: "no-store",
      });

      if (!offer.available) {
        updateState(source, offer.message || "Waiting for WebRTC offer.");
        if (Date.now() - entry.lastRestartAt > 3000) {
          entry.lastRestartAt = Date.now();
          await requestOfferRestart(source);
          updateState(source, "Requested a fresh WebRTC offer.");
        }
        return;
      }

      if (
        offer.signal_id &&
        entry.answeredSignalId &&
        offer.signal_id === entry.answeredSignalId &&
        (!entry.pc || ["failed", "disconnected", "closed"].includes(entry.pc.connectionState))
      ) {
        updateState(source, "Requesting a fresh WebRTC offer...");
        if (Date.now() - entry.lastRestartAt > 3000) {
          entry.lastRestartAt = Date.now();
          await requestOfferRestart(source);
        }
        return;
      }

      if (entry.pc) {
        try {
          entry.pc.close();
        } catch (error) {
          console.warn("Failed to close existing peer connection", error);
        }
      }

      const pc = new RTCPeerConnection({ iceServers: [] });
      entry.pc = pc;
      entry.signalId = offer.signal_id || "";

      pc.addEventListener("track", (event) => {
        const [stream] = event.streams;
        if (stream) {
          entry.stream = stream;
          entry.video.srcObject = stream;
        } else {
          const fallbackStream = new MediaStream([event.track]);
          entry.stream = fallbackStream;
          entry.video.srcObject = fallbackStream;
        }
      });

      pc.addEventListener("connectionstatechange", () => {
        updateState(source, `WebRTC ${pc.connectionState}`);
        if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
          entry.signalId = "";
        }
      });

      updateState(source, "Applying WebRTC offer...");
      await pc.setRemoteDescription({
        type: offer.sdp_type || "offer",
        sdp: offer.sdp,
      });

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await waitForIceGatheringComplete(pc, 5000);

      await fetchJson(`/api/webrtc/${encodeURIComponent(source)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          signal_id: offer.signal_id || "",
          sdp_type: pc.localDescription.type,
          sdp: pc.localDescription.sdp,
        }),
      });

      entry.answeredSignalId = offer.signal_id || "";
      updateState(source, "WebRTC answer sent. Waiting for media...");
    } catch (error) {
      updateState(source, `WebRTC error: ${error.message}`);
      if (entry.pc) {
        try {
          entry.pc.close();
        } catch (closeError) {
          console.warn("Failed to close peer after WebRTC error", closeError);
        }
        entry.pc = null;
      }
      entry.signalId = "";
    } finally {
      entry.inFlight = false;
    }
  }

  async function scanCameraPanels() {
    const panels = document.querySelectorAll("[data-webrtc-source]");
    for (const panel of panels) {
      const source = panel.getAttribute("data-webrtc-source");
      const video = panel.querySelector("video[data-webrtc-video]");
      if (!source || !video) {
        continue;
      }
      await connectSource(source, video);
    }
  }

  function startLoop() {
    scanCameraPanels().catch((error) => {
      console.error("WebRTC camera scan failed", error);
    });
    window.setInterval(() => {
      scanCameraPanels().catch((error) => {
        console.error("WebRTC camera polling failed", error);
      });
    }, 2500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startLoop, { once: true });
  } else {
    startLoop();
  }
})();
